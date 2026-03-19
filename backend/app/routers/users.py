from datetime import datetime, timedelta
import hashlib
import hmac
import secrets
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from ..db import get_session
from ..models import User, RoleEnum, PairingCode, UserBinding, UserCredential
from ..routers.deps import get_human_user, get_agent_user
from ..config import USER_TOKEN_HEADER, PAIRING_CODE_TTL_MINUTES

router = APIRouter(prefix="/users", tags=["users"])


def _pairing_expired(code: PairingCode) -> bool:
    if code.expires_at is None:
        return False
    return datetime.utcnow() > code.expires_at


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
