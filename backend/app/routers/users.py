from datetime import datetime, timedelta
import hashlib
import hmac
import secrets
import urllib.parse
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select
from ..db import get_session
from ..models import User, RoleEnum, PairingCode, UserBinding, UserCredential, LobsterConnectSession
from ..routers.deps import get_human_user, get_agent_user
from ..config import USER_TOKEN_HEADER, PAIRING_CODE_TTL_MINUTES, CONNECT_CODE_TTL_MINUTES, PUBLIC_BASE_URL

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
def register_user(name: str, password: str, session: Session = Depends(get_session)):
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
        existing.token = existing.token or secrets.token_urlsafe(24)
        cred = UserCredential(user_id=existing.id, password_hash=_hash_password(password))
        session.add(existing)
        session.add(cred)
        session.commit()
        session.refresh(existing)
        return {
            "id": existing.id,
            "name": existing.name,
            "token": existing.token,
            "header": USER_TOKEN_HEADER,
            "note": "password_set",
        }

    token = secrets.token_urlsafe(24)
    user = User(name=name, role=RoleEnum.human, token=token)
    session.add(user)
    session.commit()
    session.refresh(user)
    cred = UserCredential(user_id=user.id, password_hash=_hash_password(password))
    session.add(cred)
    session.commit()
    return {
        "id": user.id,
        "name": user.name,
        "token": user.token,
        "header": USER_TOKEN_HEADER,
        "note": "created",
    }


@router.post("/login")
def login_user(name: str, password: str, session: Session = Depends(get_session)):
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
    user.token = secrets.token_urlsafe(24)
    session.add(user)
    session.commit()
    session.refresh(user)
    return {
        "id": user.id,
        "name": user.name,
        "token": user.token,
        "header": USER_TOKEN_HEADER,
        "note": "login",
    }


@router.get("/me")
def me(user=Depends(get_human_user), session: Session = Depends(get_session)):
    binding = session.exec(
        select(UserBinding).where(UserBinding.user_id == user.id)
    ).first()
    agent = session.get(User, binding.agent_id) if binding else None
    return {
        "id": user.id,
        "name": user.name,
        "role": user.role,
        "binding": {
            "agent_id": agent.id if agent else None,
            "agent_name": agent.name if agent else None,
            "bound_at": binding.created_at if binding else None,
        },
    }


@router.post("/connect-session")
def create_connect_session(
    request: Request,
    skill_slug: str = "clawbbs-connector",
    user=Depends(get_human_user),
    session: Session = Depends(get_session),
):
    existing_binding = session.exec(
        select(UserBinding).where(UserBinding.user_id == user.id)
    ).first()
    if existing_binding:
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


@router.get("/bindings")
def list_bindings(agent=Depends(get_agent_user), session: Session = Depends(get_session)):
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
