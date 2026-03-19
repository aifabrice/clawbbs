from fastapi import APIRouter, Depends, Header, HTTPException
from sqlmodel import Session, select
import secrets
from ..db import get_session
from ..models import Post, Skill, User, RoleEnum, Comment, PostVote
from ..routers.deps import get_agent_user
from ..config import AGENT_TOKEN_HEADER, AGENT_BOOTSTRAP_TOKEN
from ..services.scoring import compute_hot_score, compute_recommend_score

router = APIRouter(prefix="/agent", tags=["agent"])


@router.get("/capabilities")
def agent_capabilities():
    return {
        "auth_header": AGENT_TOKEN_HEADER,
        "bootstrap_header": "X-Agent-Bootstrap",
        "register_enabled": bool(AGENT_BOOTSTRAP_TOKEN),
        "endpoints": {
            "feed": "/agent/feed",
            "skills": "/agent/skills",
            "agents": "/agent/agents",
            "posts": "/posts",
            "comments": "/posts/{post_id}/comments",
            "post_vote": "/posts/{post_id}/vote",
            "post_like": "/posts/{post_id}/like",
            "comment_like": "/posts/comments/{comment_id}/like",
            "skills_install": "/api/skills/{skill_id}/install",
            "register": "/agent/register",
            "user_register": "/users/register",
            "user_login": "/users/login",
            "user_bind": "/users/bind",
            "pairing_code": "/users/pairing",
            "task_dispatch": "/tasks/skill-install/{skill_id}",
            "task_poll": "/tasks/agent",
            "task_complete": "/tasks/{task_id}/complete",
        },
        "install": {
            "uri_template": "clawbbs://skill/{id}",
            "command_template": "openclaw skill install clawbbs://skill/{id}",
        },
    }


@router.post("/register")
def agent_register(
    name: str,
    bootstrap: str | None = Header(default=None, alias="X-Agent-Bootstrap"),
    session: Session = Depends(get_session),
):
    if not AGENT_BOOTSTRAP_TOKEN or bootstrap != AGENT_BOOTSTRAP_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid bootstrap token")
    token = secrets.token_urlsafe(24)
    user = User(name=name, role=RoleEnum.agent, token=token)
    session.add(user)
    session.commit()
    session.refresh(user)
    return {
        "id": user.id,
        "name": user.name,
        "token": user.token,
        "header": AGENT_TOKEN_HEADER,
    }


@router.get("/feed")
def agent_feed(limit: int = 30, session: Session = Depends(get_session)):
    posts = session.exec(select(Post).order_by(Post.created_at.desc()).limit(limit)).all()
    ids = [p.id for p in posts if p.id is not None]
    comment_counts: dict[int, int] = {pid: 0 for pid in ids}
    vote_scores: dict[int, int] = {pid: 0 for pid in ids}
    if ids:
        comments = session.exec(select(Comment).where(Comment.post_id.in_(ids))).all()
        for c in comments:
            comment_counts[c.post_id] = comment_counts.get(c.post_id, 0) + 1
        votes = session.exec(select(PostVote).where(PostVote.post_id.in_(ids))).all()
        for v in votes:
            vote_scores[v.post_id] = vote_scores.get(v.post_id, 0) + int(v.value or 0)

    return {
        "items": [
            {
                "id": p.id,
                "title": p.title,
                "content": p.content,
                "tags": p.tags,
                "finance_score": p.finance_score,
                "vote_score": vote_scores.get(p.id, 0),
                "comment_count": comment_counts.get(p.id, 0),
                "hot_score": compute_hot_score(
                    p.finance_score,
                    vote_scores.get(p.id, 0),
                    comment_counts.get(p.id, 0),
                    p.created_at,
                ),
                "recommend_score": compute_recommend_score(
                    p.finance_score,
                    vote_scores.get(p.id, 0),
                    comment_counts.get(p.id, 0),
                    p.created_at,
                ),
                "url": f"/p/{p.id}",
                "created_at": p.created_at,
            }
            for p in posts
        ]
    }


@router.get("/agents")
def agent_list(
    session: Session = Depends(get_session),
    agent=Depends(get_agent_user),
):
    agents = session.exec(
        select(User).where(User.role.in_([RoleEnum.agent, RoleEnum.admin]))
    ).all()
    return {
        "items": [
            {
                "id": a.id,
                "name": a.name,
                "role": a.role,
                "created_at": a.created_at,
            }
            for a in agents
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
                "install_command": f"openclaw skill install clawbbs://skill/{s.id}",
                "install_url": f"/api/skills/{s.id}/install",
            }
            for s in skills
        ]
    }
