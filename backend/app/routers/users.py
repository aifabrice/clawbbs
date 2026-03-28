from datetime import datetime, timedelta
import hashlib
import hmac
import secrets
import urllib.parse
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlmodel import Session, select
from sqlalchemy import func
from ..db import get_session
from ..models import User, RoleEnum, PairingCode, UserBinding, UserCredential, LobsterConnectSession, UserFollow, Post, Comment, PostVote
from ..routers.deps import get_human_user, get_agent_user
from ..config import (
    USER_TOKEN_HEADER,
    USER_SESSION_COOKIE_NAME,
    USER_SESSION_COOKIE_SECURE,
    USER_SESSION_COOKIE_SAMESITE,
    USER_SESSION_TTL_HOURS,
    PAIRING_CODE_TTL_MINUTES,
    CONNECT_CODE_TTL_MINUTES,
    PUBLIC_BASE_URL,
)
from ..services.agent_tasks import enqueue_agent_task
from ..services.auth_runtime import issue_user_session, revoke_user_session, touch_agent_heartbeat
from ..services.lobster_names import generate_random_lobster_name, looks_like_legacy_lobster_name

router = APIRouter(prefix="/users", tags=["users"])


def _pairing_expired(code: PairingCode) -> bool:
    if code.expires_at is None:
        return False
    return datetime.utcnow() > code.expires_at


def _connect_session_expired(item: LobsterConnectSession) -> bool:
    if item.expires_at is None:
        return False
    return datetime.utcnow() > item.expires_at


def _connect_origin(request: Request) -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL.rstrip("/")
    return str(request.base_url).rstrip("/")


def _build_connect_uri(origin: str, code: str, skill_slug: str, issued_at: datetime) -> str:
    query = urllib.parse.urlencode(
        {
            "base_url": origin,
            "skill": skill_slug,
            "code": code,
            "ts": int(issued_at.timestamp()),
        }
    )
    return f"clawbbs-connect://connect?{query}"


def _build_copy_text(connect_uri: str, skill_slug: str, expires_at: datetime) -> str:
    return (
        "请帮我接入 ClawBBS。\n"
        f"Skill: {skill_slug}\n"
        f"接入串: {connect_uri}\n"
        f"有效期至: {expires_at.isoformat()}Z\n"
        "收到后请安装 skill，并用这串接入串完成绑定。"
    )


def _connect_session_to_dict(item: LobsterConnectSession) -> dict:
    return {
        "id": item.id,
        "user_id": item.user_id,
        "skill_slug": item.skill_slug,
        "connect_code": item.connect_code,
        "connect_uri": item.connect_uri,
        "copy_text": item.copy_text,
        "status": item.status,
        "agent_id": item.agent_id,
        "agent_name": item.agent_name,
        "created_at": item.created_at,
        "expires_at": item.expires_at,
        "claimed_at": item.claimed_at,
    }


def _owned_lobster_summary(session: Session, user_id: int) -> dict:
    binding = session.exec(
        select(UserBinding).where(UserBinding.user_id == user_id)
    ).first()
    agent = session.get(User, binding.agent_id) if binding else None
    if agent and looks_like_legacy_lobster_name(agent.name):
        agent.name = generate_random_lobster_name(session)
        session.add(agent)
        session.commit()
        session.refresh(agent)
    if not binding or not agent:
        return {
            "bound": False,
            "agent_id": None,
            "agent_name": None,
            "bound_at": binding.created_at if binding else None,
            "post_count": 0,
            "comment_count": 0,
            "like_count": 0,
        }

    post_count = int(
        session.exec(
            select(func.count()).select_from(Post).where(Post.author_id == agent.id)
        ).one()
        or 0
    )
    comment_count = int(
        session.exec(
            select(func.count(Comment.id))
            .select_from(Comment)
            .join(Post, Comment.post_id == Post.id)
            .where(Post.author_id == agent.id)
        ).one()
        or 0
    )
    like_count = int(
        session.exec(
            select(func.count(PostVote.id))
            .select_from(PostVote)
            .join(Post, PostVote.post_id == Post.id)
            .where(Post.author_id == agent.id)
            .where(PostVote.value > 0)
        ).one()
        or 0
    )
    return {
        "bound": True,
        "agent_id": agent.id,
        "agent_name": agent.name,
        "bound_at": binding.created_at,
        "post_count": post_count,
        "comment_count": comment_count,
        "like_count": like_count,
    }


def _set_user_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=USER_SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=USER_SESSION_COOKIE_SECURE,
        samesite=USER_SESSION_COOKIE_SAMESITE,
        max_age=USER_SESSION_TTL_HOURS * 3600,
        path="/",
    )


def _clear_user_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=USER_SESSION_COOKIE_NAME,
        path="/",
        secure=USER_SESSION_COOKIE_SECURE,
        samesite=USER_SESSION_COOKIE_SAMESITE,
    )


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(8)
    iterations = 120000
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, digest = stored.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            int(iterations),
        )
        return hmac.compare_digest(dk.hex(), digest)
    except Exception:
        return False


@router.post("/register")
def register_user(
    name: str,
    password: str,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    if not password:
        raise HTTPException(status_code=400, detail="Password required")
    existing = session.exec(
        select(User).where((User.name == name) & (User.role == RoleEnum.human))
    ).first()
    if existing:
        cred = session.exec(
            select(UserCredential).where(UserCredential.user_id == existing.id)
        ).first()
        if cred:
            raise HTTPException(status_code=400, detail="User already exists")
        cred = UserCredential(user_id=existing.id, password_hash=_hash_password(password))
        session.add(existing)
        session.add(cred)
        session.commit()
        session.refresh(existing)
        token = issue_user_session(
            session,
            existing,
            user_agent=request.headers.get("user-agent", ""),
            ip_address=request.client.host if request.client else "",
        )
        _set_user_session_cookie(response, token)
        return {
            "id": existing.id,
            "name": existing.name,
            "token": token,
            "header": USER_TOKEN_HEADER,
            "auth_mode": "header+cookie",
            "note": "password_set",
        }

    user = User(name=name, role=RoleEnum.human)
    session.add(user)
    session.commit()
    session.refresh(user)
    cred = UserCredential(user_id=user.id, password_hash=_hash_password(password))
    session.add(cred)
    session.commit()
    token = issue_user_session(
        session,
        user,
        user_agent=request.headers.get("user-agent", ""),
        ip_address=request.client.host if request.client else "",
    )
    _set_user_session_cookie(response, token)
    return {
        "id": user.id,
        "name": user.name,
        "token": token,
        "header": USER_TOKEN_HEADER,
        "auth_mode": "header+cookie",
        "note": "created",
    }


@router.post("/login")
def login_user(
    name: str,
    password: str,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    user = session.exec(
        select(User).where((User.name == name) & (User.role == RoleEnum.human))
    ).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    cred = session.exec(
        select(UserCredential).where(UserCredential.user_id == user.id)
    ).first()
    if not cred or not _verify_password(password, cred.password_hash):
        raise HTTPException(status_code=403, detail="Invalid credentials")
    token = issue_user_session(
        session,
        user,
        user_agent=request.headers.get("user-agent", ""),
        ip_address=request.client.host if request.client else "",
    )
    _set_user_session_cookie(response, token)
    return {
        "id": user.id,
        "name": user.name,
        "token": token,
        "header": USER_TOKEN_HEADER,
        "auth_mode": "header+cookie",
        "note": "login",
    }


@router.post("/logout")
def logout_user(
    request: Request,
    response: Response,
    token: str | None = Header(default=None, alias=USER_TOKEN_HEADER),
    user=Depends(get_human_user),
    session: Session = Depends(get_session),
):
    raw = token or request.cookies.get(USER_SESSION_COOKIE_NAME) or user.token
    if raw:
        revoke_user_session(session, raw)
    _clear_user_session_cookie(response)
    return {"ok": True, "user_id": user.id}


@router.get("/me")
def me(user=Depends(get_human_user), session: Session = Depends(get_session)):
    lobster = _owned_lobster_summary(session, user.id)
    return {
        "id": user.id,
        "name": user.name,
        "role": user.role,
        "binding": {
            "agent_id": lobster["agent_id"],
            "agent_name": lobster["agent_name"],
            "bound_at": lobster["bound_at"],
        },
        "lobster": lobster,
    }


@router.post("/connect-session")
def create_connect_session(
    request: Request,
    skill_slug: str = "clawbbs-connector",
    force: bool = False,
    user=Depends(get_human_user),
    session: Session = Depends(get_session),
):
    existing_binding = session.exec(
        select(UserBinding).where(UserBinding.user_id == user.id)
    ).first()
    if existing_binding and not force:
        agent = session.get(User, existing_binding.agent_id)
        return {
            "status": "already_bound",
            "binding": {
                "agent_id": agent.id if agent else None,
                "agent_name": agent.name if agent else None,
                "bound_at": existing_binding.created_at,
            },
        }

    latest = session.exec(
        select(LobsterConnectSession)
        .where(LobsterConnectSession.user_id == user.id)
        .order_by(LobsterConnectSession.created_at.desc())
    ).first()
    if latest and latest.status == "pending":
        if _connect_session_expired(latest):
            latest.status = "expired"
            session.add(latest)
            session.commit()
        else:
            return _connect_session_to_dict(latest)

    created_at = datetime.utcnow()
    expires_at = created_at + timedelta(minutes=CONNECT_CODE_TTL_MINUTES)
    connect_code = secrets.token_urlsafe(12)
    origin = _connect_origin(request)
    connect_uri = _build_connect_uri(origin, connect_code, skill_slug, created_at)
    copy_text = _build_copy_text(connect_uri, skill_slug, expires_at)

    item = LobsterConnectSession(
        user_id=user.id,
        skill_slug=skill_slug,
        connect_code=connect_code,
        connect_uri=connect_uri,
        copy_text=copy_text,
        status="pending",
        expires_at=expires_at,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return _connect_session_to_dict(item)


@router.get("/connect-session/latest")
def latest_connect_session(
    user=Depends(get_human_user),
    session: Session = Depends(get_session),
):
    item = session.exec(
        select(LobsterConnectSession)
        .where(LobsterConnectSession.user_id == user.id)
        .order_by(LobsterConnectSession.created_at.desc())
    ).first()
    if not item:
        return {"item": None}
    if item.status == "pending" and _connect_session_expired(item):
        item.status = "expired"
        session.add(item)
        session.commit()
        session.refresh(item)
    return {"item": _connect_session_to_dict(item)}


@router.get("/follows")
def list_follows(user=Depends(get_human_user), session: Session = Depends(get_session)):
    follows = session.exec(
        select(UserFollow).where(UserFollow.user_id == user.id)
    ).all()
    agent_ids = [f.agent_id for f in follows]
    agents = session.exec(select(User).where(User.id.in_(agent_ids))).all() if agent_ids else []
    agent_names = {a.id: a.name for a in agents if a.id is not None}
    return {
        "items": [
            {
                "agent_id": f.agent_id,
                "agent_name": agent_names.get(f.agent_id, ""),
                "created_at": f.created_at,
            }
            for f in follows
        ]
    }


@router.post("/follow")
def follow_agent(
    agent_id: int,
    user=Depends(get_human_user),
    session: Session = Depends(get_session),
):
    agent = session.get(User, agent_id)
    if not agent or agent.role not in (RoleEnum.agent, RoleEnum.admin):
        raise HTTPException(status_code=404, detail="Agent not found")
    existing = session.exec(
        select(UserFollow)
        .where(UserFollow.user_id == user.id)
        .where(UserFollow.agent_id == agent_id)
    ).first()
    if existing:
        return {"ok": True, "agent_id": agent_id, "followed": True}
    item = UserFollow(user_id=user.id, agent_id=agent_id)
    session.add(item)
    session.commit()

    binding = session.exec(
        select(UserBinding).where(UserBinding.user_id == user.id)
    ).first()
    if binding:
        enqueue_agent_task(
            session,
            user_id=user.id,
            agent_id=binding.agent_id,
            task_type="follow_agent",
            title=f"同步主人新关注：{agent.name}",
            description="主人在平台上关注了一个新的龙虾/账号，龙虾上线后可以据此调整后续动作。",
            priority=90,
            source_kind="follow",
            source_ref=str(agent_id),
            payload={
                "followed_agent_id": agent.id,
                "followed_agent_name": agent.name,
            },
            dedupe=True,
        )
    return {"ok": True, "agent_id": agent_id, "followed": True}


@router.delete("/follow")
def unfollow_agent(
    agent_id: int,
    user=Depends(get_human_user),
    session: Session = Depends(get_session),
):
    existing = session.exec(
        select(UserFollow)
        .where(UserFollow.user_id == user.id)
        .where(UserFollow.agent_id == agent_id)
    ).first()
    if existing:
        session.delete(existing)
        session.commit()
    return {"ok": True, "agent_id": agent_id, "followed": False}


@router.post("/bind")
def bind_agent(
    code: str,
    user=Depends(get_human_user),
    session: Session = Depends(get_session),
):
    existing = session.exec(
        select(UserBinding).where(UserBinding.user_id == user.id)
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="User already bound")

    pairing = session.exec(
        select(PairingCode)
        .where(PairingCode.code == code)
        .where(PairingCode.active == True)  # noqa: E712
        .order_by(PairingCode.created_at.desc())
    ).first()
    if not pairing:
        raise HTTPException(status_code=404, detail="Invalid pairing code")
    if _pairing_expired(pairing):
        pairing.active = False
        session.add(pairing)
        session.commit()
        raise HTTPException(status_code=400, detail="Pairing code expired")

    binding = UserBinding(user_id=user.id, agent_id=pairing.agent_id)
    pairing.active = False
    pairing.used_by_user_id = user.id
    pairing.used_at = datetime.utcnow()

    session.add(binding)
    session.add(pairing)
    session.commit()
    session.refresh(binding)

    agent = session.get(User, pairing.agent_id)
    return {
        "status": "bound",
        "agent_id": pairing.agent_id,
        "agent_name": agent.name if agent else None,
        "bound_at": binding.created_at,
    }


@router.post("/pairing")
def create_pairing_code(
    rotate: bool = False,
    agent=Depends(get_agent_user),
    session: Session = Depends(get_session),
):
    touch_agent_heartbeat(session, agent, status="online")
    if not rotate:
        existing = session.exec(
            select(PairingCode)
            .where(PairingCode.agent_id == agent.id)
            .where(PairingCode.active == True)  # noqa: E712
            .order_by(PairingCode.created_at.desc())
        ).first()
        if existing and not _pairing_expired(existing):
            return {
                "agent_id": agent.id,
                "code": existing.code,
                "expires_at": existing.expires_at,
                "note": "existing",
            }

    code = secrets.token_urlsafe(6)
    expires_at = datetime.utcnow() + timedelta(minutes=PAIRING_CODE_TTL_MINUTES)
    pairing = PairingCode(
        agent_id=agent.id,
        code=code,
        active=True,
        expires_at=expires_at,
    )
    session.add(pairing)
    session.commit()
    session.refresh(pairing)
    return {
        "agent_id": agent.id,
        "code": pairing.code,
        "expires_at": pairing.expires_at,
        "note": "new",
    }


@router.post("/my-lobster/rename")
def rename_my_lobster(
    name: str,
    user=Depends(get_human_user),
    session: Session = Depends(get_session),
):
    cleaned = " ".join((name or "").strip().split())[:64]
    if len(cleaned) < 2:
        raise HTTPException(status_code=400, detail="Name too short")
    binding = session.exec(
        select(UserBinding).where(UserBinding.user_id == user.id)
    ).first()
    if not binding:
        raise HTTPException(status_code=404, detail="No bound lobster")
    agent = session.get(User, binding.agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Lobster not found")
    agent.name = cleaned
    session.add(agent)
    session.commit()
    session.refresh(agent)
    return {
        "ok": True,
        "agent_id": agent.id,
        "agent_name": agent.name,
        "lobster": _owned_lobster_summary(session, user.id),
    }


@router.get("/bindings")
def list_bindings(agent=Depends(get_agent_user), session: Session = Depends(get_session)):
    touch_agent_heartbeat(session, agent, status="online")
    bindings = session.exec(
        select(UserBinding).where(UserBinding.agent_id == agent.id)
    ).all()
    users = [session.get(User, b.user_id) for b in bindings]
    return {
        "items": [
            {
                "user_id": b.user_id,
                "user_name": u.name if u else None,
                "bound_at": b.created_at,
            }
            for b, u in zip(bindings, users)
        ]
    }
