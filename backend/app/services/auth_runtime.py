from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from ..config import AGENT_TOKEN_TTL_DAYS, USER_SESSION_TTL_HOURS
from ..models import (
    AgentHeartbeat,
    AgentSkillInstallation,
    AgentToken,
    AuthSession,
    RoleEnum,
    User,
)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_user_session(
    session: Session,
    user: User,
    *,
    user_agent: str = "",
    ip_address: str = "",
) -> str:
    raw = secrets.token_urlsafe(24)
    now = datetime.utcnow()
    expires_at = now + timedelta(hours=USER_SESSION_TTL_HOURS)
    record = AuthSession(
        user_id=user.id,
        token_hash=hash_token(raw),
        status="active",
        user_agent=(user_agent or "")[:255],
        ip_address=(ip_address or "")[:64],
        created_at=now,
        expires_at=expires_at,
        last_used_at=now,
    )
    user.token = raw  # legacy compatibility
    session.add(user)
    session.add(record)
    session.commit()
    session.refresh(record)
    return raw


def revoke_user_session(session: Session, raw_token: str) -> bool:
    item = session.exec(
        select(AuthSession).where(AuthSession.token_hash == hash_token(raw_token))
    ).first()
    if not item:
        return False
    item.status = "revoked"
    item.revoked_at = datetime.utcnow()
    session.add(item)
    session.commit()
    return True


def resolve_human_user(session: Session, raw_token: str | None) -> User | None:
    if not raw_token:
        return None
    now = datetime.utcnow()
    item = session.exec(
        select(AuthSession)
        .where(AuthSession.token_hash == hash_token(raw_token))
        .order_by(AuthSession.created_at.desc())
    ).first()
    if item:
        if item.status != "active":
            return None
        if item.expires_at and now > item.expires_at:
            item.status = "expired"
            session.add(item)
            session.commit()
            return None
        user = session.get(User, item.user_id)
        if not user or user.role not in (RoleEnum.human, RoleEnum.admin):
            return None
        item.last_used_at = now
        session.add(item)
        session.commit()
        return user

    legacy = session.exec(select(User).where(User.token == raw_token)).first()
    if legacy and legacy.role in (RoleEnum.human, RoleEnum.admin):
        return legacy
    return None


def issue_agent_token(
    session: Session,
    agent: User,
    *,
    label: str = "default",
    last_ip: str = "",
) -> str:
    raw = secrets.token_urlsafe(24)
    now = datetime.utcnow()
    expires_at = now + timedelta(days=AGENT_TOKEN_TTL_DAYS)
    record = AgentToken(
        agent_id=agent.id,
        token_hash=hash_token(raw),
        label=label[:64],
        status="active",
        created_at=now,
        expires_at=expires_at,
        last_used_at=now,
        last_ip=(last_ip or "")[:64],
    )
    agent.token = raw  # legacy compatibility
    session.add(agent)
    session.add(record)
    session.commit()
    session.refresh(record)
    return raw


def resolve_agent_user(session: Session, raw_token: str | None) -> User | None:
    if not raw_token:
        return None
    now = datetime.utcnow()
    item = session.exec(
        select(AgentToken)
        .where(AgentToken.token_hash == hash_token(raw_token))
        .order_by(AgentToken.created_at.desc())
    ).first()
    if item:
        if item.status != "active":
            return None
        if item.expires_at and now > item.expires_at:
            item.status = "expired"
            session.add(item)
            session.commit()
            return None
        user = session.get(User, item.agent_id)
        if not user or user.role not in (RoleEnum.agent, RoleEnum.admin):
            return None
        item.last_used_at = now
        session.add(item)
        session.commit()
        return user

    legacy = session.exec(select(User).where(User.token == raw_token)).first()
    if legacy and legacy.role in (RoleEnum.agent, RoleEnum.admin):
        return legacy
    return None


def touch_agent_heartbeat(
    session: Session,
    agent: User,
    *,
    status: str = "online",
    app_version: str = "",
    os_name: str = "",
    capabilities: dict[str, Any] | None = None,
) -> AgentHeartbeat:
    item = session.exec(
        select(AgentHeartbeat).where(AgentHeartbeat.agent_id == agent.id)
    ).first()
    now = datetime.utcnow()
    if not item:
        item = AgentHeartbeat(
            agent_id=agent.id,
            status=status,
            app_version=app_version,
            os_name=os_name,
            capabilities=capabilities or {},
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
    else:
        item.status = status or item.status
        if app_version:
            item.app_version = app_version
        if os_name:
            item.os_name = os_name
        if capabilities is not None:
            item.capabilities = capabilities
        item.last_seen_at = now
        item.updated_at = now
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def upsert_agent_skill_installation(
    session: Session,
    *,
    agent_id: int,
    skill_id: int,
    status: str,
    installed_version: str = "",
    install_source: str = "task",
    last_result: str = "",
) -> AgentSkillInstallation:
    item = session.exec(
        select(AgentSkillInstallation)
        .where(AgentSkillInstallation.agent_id == agent_id)
        .where(AgentSkillInstallation.skill_id == skill_id)
        .order_by(AgentSkillInstallation.updated_at.desc())
    ).first()
    now = datetime.utcnow()
    if not item:
        item = AgentSkillInstallation(
            agent_id=agent_id,
            skill_id=skill_id,
            status=status,
            installed_version=installed_version,
            install_source=install_source,
            last_result=last_result,
            installed_at=now if status == "installed" else None,
            created_at=now,
            updated_at=now,
        )
    else:
        item.status = status
        if installed_version:
            item.installed_version = installed_version
        if install_source:
            item.install_source = install_source
        item.last_result = last_result
        if status == "installed":
            item.installed_at = now
        item.updated_at = now
    session.add(item)
    session.commit()
    session.refresh(item)
    return item
