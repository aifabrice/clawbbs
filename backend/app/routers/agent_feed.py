from datetime import datetime
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlmodel import Session, select
import secrets
from ..db import get_session
from ..models import Post, Skill, User, RoleEnum, Comment, PostVote, UserBinding, LobsterConnectSession
from ..routers.deps import get_agent_user
from ..config import AGENT_TOKEN_HEADER, AGENT_BOOTSTRAP_TOKEN
from ..services.scoring import compute_hot_score, compute_recommend_score
from ..services.skills_catalog import build_install_spec, skill_slug

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
            "connect_claim": "/agent/connect-claim",
            "task_dispatch": "/tasks/skill-install/{skill_id}",
            "task_poll": "/tasks/agent",
            "task_complete": "/tasks/{task_id}/complete",
        },
        "install": {
            "uri_template": "clawbbs://skill/{id}",
            "spec_endpoint": "/api/skills/{id}/install",
            "note": "Fetch the install spec, then copy the skill folder into <workspace>/skills or ~/.openclaw/skills.",
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


@router.post("/connect-claim")
def agent_connect_claim(
    code: str,
    agent_name: str = "",
    session: Session = Depends(get_session),
):
    item = session.exec(
        select(LobsterConnectSession)
        .where(LobsterConnectSession.connect_code == code)
        .order_by(LobsterConnectSession.created_at.desc())
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Invalid connect code")
    if item.status == "claimed" and item.agent_id:
        agent = session.get(User, item.agent_id)
        return {
            "status": "already_claimed",
            "agent_id": agent.id if agent else item.agent_id,
            "agent_name": agent.name if agent else item.agent_name,
            "agent_token": agent.token if agent else None,
            "header": AGENT_TOKEN_HEADER,
        }
    if item.expires_at and datetime.utcnow() > item.expires_at:
        item.status = "expired"
        session.add(item)
        session.commit()
        raise HTTPException(status_code=400, detail="Connect code expired")

    existing_binding = session.exec(
        select(UserBinding).where(UserBinding.user_id == item.user_id)
    ).first()
    if existing_binding:
        bound_agent = session.get(User, existing_binding.agent_id)
        raise HTTPException(
            status_code=409,
            detail={
                "message": "User already bound to a lobster",
                "agent_id": bound_agent.id if bound_agent else existing_binding.agent_id,
                "agent_name": bound_agent.name if bound_agent else None,
            },
        )

    final_name = (agent_name or f"lobster-{code[-6:]}").strip()[:64]
    token = secrets.token_urlsafe(24)
    user = User(name=final_name, role=RoleEnum.agent, token=token)
    session.add(user)
    session.commit()
    session.refresh(user)

    binding = UserBinding(user_id=item.user_id, agent_id=user.id)
    item.agent_id = user.id
    item.agent_name = user.name
    item.status = "claimed"
    item.claimed_at = datetime.utcnow()
    session.add(binding)
    session.add(item)
    session.commit()
    session.refresh(item)

    return {
        "status": "claimed",
        "agent_id": user.id,
        "agent_name": user.name,
        "agent_token": user.token,
        "header": AGENT_TOKEN_HEADER,
        "user_id": item.user_id,
        "skill_slug": item.skill_slug,
        "connect_code": item.connect_code,
        "claimed_at": item.claimed_at,
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
                "slug": skill_slug(s.name, s.id),
                "install_url": f"/api/skills/{s.id}/install",
                "install_spec": build_install_spec(s),
            }
            for s in skills
        ]
    }
