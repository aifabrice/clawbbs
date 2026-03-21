from fastapi import Header, HTTPException, Depends
from sqlmodel import Session
from ..db import get_session
from ..models import User, RoleEnum
from ..config import AGENT_TOKEN_HEADER, USER_TOKEN_HEADER
from ..services.auth_runtime import resolve_agent_user, resolve_human_user


def get_agent_user(
    token: str | None = Header(default=None, alias=AGENT_TOKEN_HEADER),
    session: Session = Depends(get_session),
) -> User:
    if not token:
        raise HTTPException(status_code=401, detail="Missing agent token")
    user = resolve_agent_user(session, token)
    if not user or user.role not in (RoleEnum.agent, RoleEnum.admin):
        raise HTTPException(status_code=403, detail="Invalid agent token")
    return user


def get_human_user(
    token: str | None = Header(default=None, alias=USER_TOKEN_HEADER),
    session: Session = Depends(get_session),
) -> User:
    if not token:
        raise HTTPException(status_code=401, detail="Missing user token")
    user = resolve_human_user(session, token)
    if not user or user.role not in (RoleEnum.human, RoleEnum.admin):
        raise HTTPException(status_code=403, detail="Invalid user token")
    return user


def get_admin_user(
    token: str | None = Header(default=None, alias=USER_TOKEN_HEADER),
    session: Session = Depends(get_session),
) -> User:
    if not token:
        raise HTTPException(status_code=401, detail="Missing user token")
    user = resolve_human_user(session, token)
    if not user or user.role != RoleEnum.admin:
        raise HTTPException(status_code=403, detail="Admin only")
    return user
