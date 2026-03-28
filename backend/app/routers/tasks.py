from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..config import AGENT_TASK_LEASE_SECONDS
from ..db import get_session
from ..models import AgentTask, AgentTaskCreate, Skill, UserBinding
from ..routers.deps import get_agent_user, get_human_user
from ..services.agent_tasks import (
    claim_agent_task,
    complete_agent_task,
    enqueue_agent_task,
    reclaim_expired_task,
    serialize_agent_task,
)
from ..services.auth_runtime import touch_agent_heartbeat, upsert_agent_skill_installation
from ..services.skills_catalog import build_install_spec, skill_slug

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _get_binding(session: Session, user_id: int) -> UserBinding | None:
    return session.exec(select(UserBinding).where(UserBinding.user_id == user_id)).first()


@router.post("/skill-install/{skill_id}")
def dispatch_skill_install(
    skill_id: int,
    user=Depends(get_human_user),
    session: Session = Depends(get_session),
):
    skill = session.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    binding = _get_binding(session, user.id)
    if not binding:
        raise HTTPException(status_code=400, detail="User not bound to agent")

    payload = {
        "skill_id": skill.id,
        "skill_name": skill.name,
        "skill_slug": skill_slug(skill.name, skill.id),
        "install_url": f"/api/skills/{skill.id}/install",
        "install_spec": build_install_spec(skill),
    }
    task = enqueue_agent_task(
        session,
        user_id=user.id,
        agent_id=binding.agent_id,
        task_type="skill_install",
        title=f"安装 Skill：{skill.name}",
        description=skill.description or "主人在平台上给龙虾下发了一个 Skill 安装任务。",
        priority=20,
        source_kind="skill",
        source_ref=str(skill.id),
        payload=payload,
        dedupe=True,
    )
    upsert_agent_skill_installation(
        session,
        agent_id=binding.agent_id,
        skill_id=skill_id,
        status="pending",
        install_source="task",
        last_result=f"task #{task.id} queued",
    )
    return {
        "task_id": task.id,
        "status": task.status,
        "skill_id": skill.id,
        "skill_name": skill.name,
        "skill_slug": payload["skill_slug"],
        "agent_id": task.agent_id,
        "install_url": payload["install_url"],
        "install_spec": payload["install_spec"],
        "task": serialize_agent_task(session, task),
    }


@router.post("/todo")
def create_owner_todo(
    body: AgentTaskCreate,
    user=Depends(get_human_user),
    session: Session = Depends(get_session),
):
    binding = _get_binding(session, user.id)
    if not binding:
        raise HTTPException(status_code=400, detail="User not bound to agent")
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title required")
    task = enqueue_agent_task(
        session,
        user_id=user.id,
        agent_id=binding.agent_id,
        task_type="owner_todo",
        title=title[:120],
        description=(body.description or "")[:1000],
        priority=max(1, min(int(body.priority or 100), 999)),
        source_kind="owner_todo",
        source_ref="",
        payload=body.payload or {},
        dedupe=False,
    )
    return {"ok": True, "task": serialize_agent_task(session, task)}


@router.get("/agent")
def list_agent_tasks(
    limit: int = 20,
    agent=Depends(get_agent_user),
    session: Session = Depends(get_session),
):
    touch_agent_heartbeat(session, agent, status="online")
    tasks = session.exec(
        select(AgentTask)
        .where(AgentTask.agent_id == agent.id)
        .where(AgentTask.status.in_(["pending", "claimed"]))
        .order_by(AgentTask.priority.asc(), AgentTask.created_at.asc())
        .limit(max(1, min(limit, 100)))
    ).all()

    items = []
    for task in tasks:
        task = reclaim_expired_task(session, task)
        items.append(serialize_agent_task(session, task))
    return {"items": items}


@router.post("/{task_id}/claim")
def claim_task(
    task_id: int,
    lease_seconds: int = AGENT_TASK_LEASE_SECONDS,
    agent=Depends(get_agent_user),
    session: Session = Depends(get_session),
):
    task = session.get(AgentTask, task_id)
    if not task or task.agent_id != agent.id:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status in ("done", "failed", "cancelled"):
        raise HTTPException(status_code=400, detail="Task already finished")
    task = claim_agent_task(session, task, lease_seconds=lease_seconds)
    touch_agent_heartbeat(session, agent, status="online")
    return {"ok": True, "task": serialize_agent_task(session, task)}


@router.post("/{task_id}/complete")
def complete_task(
    task_id: int,
    status: str = "done",
    result: str = "",
    agent=Depends(get_agent_user),
    session: Session = Depends(get_session),
):
    task = session.get(AgentTask, task_id)
    if not task or task.agent_id != agent.id:
        raise HTTPException(status_code=404, detail="Task not found")
    if status not in ("done", "failed"):
        raise HTTPException(status_code=400, detail="Invalid status")
    task = complete_agent_task(session, task, status=status, result=result)
    if task.task_type == "skill_install":
        skill_id = (task.payload or {}).get("skill_id")
        if skill_id:
            upsert_agent_skill_installation(
                session,
                agent_id=agent.id,
                skill_id=int(skill_id),
                status="installed" if status == "done" else "failed",
                install_source="task",
                last_result=result or f"task #{task.id} {status}",
            )
    touch_agent_heartbeat(session, agent, status="online")
    return {"ok": True, "task": serialize_agent_task(session, task)}


@router.get("/me")
def list_user_tasks(
    limit: int = 30,
    user=Depends(get_human_user),
    session: Session = Depends(get_session),
):
    tasks = session.exec(
        select(AgentTask)
        .where(AgentTask.user_id == user.id)
        .order_by(AgentTask.created_at.desc())
        .limit(max(1, min(limit, 100)))
    ).all()
    items = [serialize_agent_task(session, t) for t in tasks]
    return {"items": items}
