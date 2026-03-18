from fastapi import APIRouter, Depends
from sqlmodel import Session, select
from ..db import get_session
from ..models import Post, Skill

router = APIRouter(prefix="/agent", tags=["agent"])


@router.get("/feed")
def agent_feed(limit: int = 30, session: Session = Depends(get_session)):
    posts = (
        session.exec(select(Post).order_by(Post.created_at.desc()).limit(limit)).all()
    )
    return {
        "items": [
            {
                "id": p.id,
                "title": p.title,
                "content": p.content,
                "tags": p.tags,
                "finance_score": p.finance_score,
                "url": f"/p/{p.id}",
                "created_at": p.created_at,
            }
            for p in posts
        ]
    }


@router.get("/skills")
def agent_skills(limit: int = 50, session: Session = Depends(get_session)):
    skills = session.exec(select(Skill).order_by(Skill.id.desc()).limit(limit)).all()
    return {
        "items": [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "owner_id": s.owner_id,
            }
            for s in skills
        ]
    }
