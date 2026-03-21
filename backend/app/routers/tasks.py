from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from ..db import get_session
from ..models import Skill, UserBinding, SkillInstallTask
from ..routers.deps import get_human_user, get_agent_user
from ..services.skills_catalog import build_install_spec, skill_slug

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("/skill-install/{skill_id}")
def dispatch_skill_install(
    skill_id: int,
    user=Depends(get_human_user),
    session: Session = Depends(get_session),
):
    skill = session.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    binding = session.exec(
        select(UserBinding).where(UserBinding.user_id == user.id)
    ).first()
    if not binding:
        raise HTTPException(status_code=400, detail="User not bound to agent")

    task = SkillInstallTask(
        skill_id=skill_id,
        user_id=user.id,
        agent_id=binding.agent_id,
        status="pending",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return {
        "task_id": task.id,
        "status": task.status,
        "skill_id": task.skill_id,
        "skill_name": skill.name,
        "skill_slug": skill_slug(skill.name, skill.id),
        "agent_id": task.agent_id,
        "install_url": f"/api/skills/{skill.id}/install",
        "install_spec": build_install_spec(skill),
    }


@router.get("/agent")
def list_agent_tasks(
    limit: int = 10,
    agent=Depends(get_agent_user),
    session: Session = Depends(get_session),
):
    tasks = session.exec(
        select(SkillInstallTask)
        .where(SkillInstallTask.agent_id == agent.id)
        .where(SkillInstallTask.status == "pending")
        .order_by(SkillInstallTask.created_at.asc())
        .limit(limit)
    ).all()

    items = []
    for t in tasks:
        skill = session.get(Skill, t.skill_id)
        items.append(
            {
                "id": t.id,
                "skill_id": t.skill_id,
                "skill_name": skill.name if skill else None,
                "skill_slug": skill_slug(skill.name, skill.id) if skill else None,
                "user_id": t.user_id,
                "status": t.status,
                "created_at": t.created_at,
                "install_url": f"/api/skills/{t.skill_id}/install",
                "install_spec": build_install_spec(skill) if skill else None,
            }
        )
    return {"items": items}


@router.post("/{task_id}/complete")
def complete_task(
    task_id: int,
    status: str = "done",
    result: str = "",
    agent=Depends(get_agent_user),
    session: Session = Depends(get_session),
):
    task = session.get(SkillInstallTask, task_id)
    if not task or task.agent_id != agent.id:
        raise HTTPException(status_code=404, detail="Task not found")
    if status not in ("done", "failed"):
        raise HTTPException(status_code=400, detail="Invalid status")
    task.status = status
    task.result = result
    task.updated_at = datetime.utcnow()
    session.add(task)
    session.commit()
    session.refresh(task)
    return {
        "id": task.id,
        "status": task.status,
        "result": task.result,
        "updated_at": task.updated_at,
    }


@router.get("/me")
def list_user_tasks(
    limit: int = 10,
    user=Depends(get_human_user),
    session: Session = Depends(get_session),
):
    tasks = session.exec(
        select(SkillInstallTask)
        .where(SkillInstallTask.user_id == user.id)
        .order_by(SkillInstallTask.created_at.desc())
        .limit(limit)
    ).all()
    items = []
    for t in tasks:
        skill = session.get(Skill, t.skill_id)
        items.append(
            {
                "id": t.id,
                "skill_id": t.skill_id,
                "skill_name": skill.name if skill else None,
                "skill_slug": skill_slug(skill.name, skill.id) if skill else None,
                "agent_id": t.agent_id,
                "status": t.status,
                "result": t.result,
                "created_at": t.created_at,
                "updated_at": t.updated_at,
            }
        )
    return {"items": items}
