from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from ..db import get_session
from ..models import Skill, SkillVersion, SkillTest
from ..routers.deps import get_agent_user

router = APIRouter(prefix="/skills", tags=["skills"])


@router.get("")
def list_skills(session: Session = Depends(get_session)):
    return session.exec(select(Skill).order_by(Skill.id.desc())).all()


@router.post("")
def create_skill(
    name: str,
    description: str = "",
    session: Session = Depends(get_session),
    agent=Depends(get_agent_user),
):
    skill = Skill(name=name, description=description, owner_id=agent.id)
    session.add(skill)
    session.commit()
    session.refresh(skill)
    return skill


@router.post("/{skill_id}/versions")
def create_skill_version(
    skill_id: int,
    version: str,
    changelog: str = "",
    session: Session = Depends(get_session),
    agent=Depends(get_agent_user),
):
    skill = session.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    sv = SkillVersion(skill_id=skill_id, version=version, changelog=changelog)
    session.add(sv)
    session.commit()
    session.refresh(sv)
    return sv


@router.post("/{skill_id}/tests")
def create_skill_test(
    skill_id: int,
    skill_version_id: int,
    result: str,
    metrics: dict | None = None,
    session: Session = Depends(get_session),
    agent=Depends(get_agent_user),
):
    skill = session.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    st = SkillTest(
        skill_version_id=skill_version_id,
        tester_id=agent.id,
        result=result,
        metrics=metrics or {},
    )
    session.add(st)
    session.commit()
    session.refresh(st)
    return st
