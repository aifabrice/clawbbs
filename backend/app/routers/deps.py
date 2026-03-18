from fastapi import Header, HTTPException, Depends
from sqlmodel import Session, select
from ..db import get_session
from ..models import User, RoleEnum
from ..config import AGENT_TOKEN_HEADER


def get_agent_user(
    token: str | None = Header(default=None, alias=AGENT_TOKEN_HEADER),
    session: Session = Depends(get_session),
) -> User:
    if not token:
        raise HTTPException(status_code=401, detail="Missing agent token")
    user = session.exec(select(User).where(User.token == token)).first()
    if not user or user.role not in (RoleEnum.agent, RoleEnum.admin):
        raise HTTPException(status_code=403, detail="Invalid agent token")
    return user
