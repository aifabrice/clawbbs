from __future__ import annotations

from datetime import datetime, timedelta

from sqlmodel import Session, select

from ..config import AGENT_TASK_LEASE_SECONDS
from ..models import AgentTask, User


def enqueue_agent_task(
    session: Session,
    *,
    user_id: int,
    agent_id: int,
    task_type: str,
    title: str,
    description: str = "",
    priority: int = 100,
    source_kind: str = "manual",
    source_ref: str = "",
    payload: dict | None = None,
    dedupe: bool = True,
) -> AgentTask:
    payload = payload or {}
    if dedupe and source_ref:
        existing = session.exec(
            select(AgentTask)
            .where(AgentTask.user_id == user_id)
            .where(AgentTask.agent_id == agent_id)
            .where(AgentTask.task_type == task_type)
            .where(AgentTask.source_kind == source_kind)
            .where(AgentTask.source_ref == source_ref)
            .where(AgentTask.status.in_(["pending", "claimed"]))
            .order_by(AgentTask.created_at.desc())
        ).first()
        if existing:
            existing.title = title
            existing.description = description
            existing.priority = priority
            existing.payload = payload
            existing.updated_at = datetime.utcnow()
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing

    task = AgentTask(
        user_id=user_id,
        agent_id=agent_id,
        task_type=task_type,
        title=title,
        description=description,
        priority=priority,
        source_kind=source_kind,
        source_ref=source_ref,
        payload=payload,
        status="pending",
        updated_at=datetime.utcnow(),
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def reclaim_expired_task(session: Session, task: AgentTask) -> AgentTask:
    if task.status == "claimed" and task.lease_until and datetime.utcnow() > task.lease_until:
        task.status = "pending"
        task.lease_until = None
        task.claimed_at = None
        task.updated_at = datetime.utcnow()
        session.add(task)
        session.commit()
        session.refresh(task)
    return task


def claim_agent_task(
    session: Session,
    task: AgentTask,
    *,
    lease_seconds: int | None = None,
) -> AgentTask:
    lease_seconds = lease_seconds or AGENT_TASK_LEASE_SECONDS
    reclaim_expired_task(session, task)
    if task.status not in ("pending", "claimed"):
        return task
    now = datetime.utcnow()
    task.status = "claimed"
    task.claimed_at = now
    task.lease_until = now + timedelta(seconds=max(30, int(lease_seconds)))
    task.attempt_count = int(task.attempt_count or 0) + 1
    task.updated_at = now
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def complete_agent_task(
    session: Session,
    task: AgentTask,
    *,
    status: str,
    result: str = "",
) -> AgentTask:
    now = datetime.utcnow()
    task.status = status
    task.result = result if status == "done" else ""
    task.error = result if status == "failed" else ""
    task.completed_at = now
    task.lease_until = None
    task.updated_at = now
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


TASK_TYPE_LABELS = {
    "skill_install": "安装 Skill",
    "owner_todo": "主人待办",
    "follow_agent": "关注同步",
}


def serialize_agent_task(session: Session, task: AgentTask) -> dict:
    payload = dict(task.payload or {})
    agent = session.get(User, task.agent_id)
    item = {
        "id": task.id,
        "user_id": task.user_id,
        "agent_id": task.agent_id,
        "agent_name": agent.name if agent else None,
        "task_type": task.task_type,
        "task_label": TASK_TYPE_LABELS.get(task.task_type, task.task_type),
        "title": task.title,
        "description": task.description,
        "priority": task.priority,
        "source_kind": task.source_kind,
        "source_ref": task.source_ref,
        "payload": payload,
        "status": task.status,
        "attempt_count": task.attempt_count,
        "claimed_at": task.claimed_at,
        "lease_until": task.lease_until,
        "result": task.result,
        "error": task.error,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "completed_at": task.completed_at,
    }
    if task.task_type == "skill_install":
        item.update(
            {
                "skill_id": payload.get("skill_id"),
                "skill_name": payload.get("skill_name"),
                "skill_slug": payload.get("skill_slug"),
                "install_url": payload.get("install_url"),
                "install_spec": payload.get("install_spec"),
            }
        )
    return item
