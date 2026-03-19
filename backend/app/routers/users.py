from datetime import datetime, timedelta
import secrets
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from ..db import get_session
from ..models import User, RoleEnum, PairingCode, UserBinding
from ..routers.deps import get_human_user, get_agent_user
from ..config import USER_TOKEN_HEADER, PAIRING_CODE_TTL_MINUTES

router = APIRouter(prefix="/users", tags=["users"])


def _pairing_expired(code: PairingCode) -> bool:
    if code.expires_at is None:
        return False
    return datetime.utcnow() > code.expires_at


@router.post("/register")
def register_user(name: str, session: Session = Depends(get_session)):
    existing = session.exec(
        select(User).where((User.name == name) & (User.role == RoleEnum.human))
    ).first()
    if existing:
        return {
            "id": existing.id,
            "name": existing.name,
            "token": existing.token,
            "header": USER_TOKEN_HEADER,
            "note": "existing_user",
        }
    token = secrets.token_urlsafe(24)
    user = User(name=name, role=RoleEnum.human, token=token)
    session.add(user)
    session.commit()
    session.refresh(user)
    return {
        "id": user.id,
        "name": user.name,
        "token": user.token,
        "header": USER_TOKEN_HEADER,
        "note": "created",
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
