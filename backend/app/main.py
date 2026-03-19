from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from .db import init_db, engine
from .models import (
    Post,
    Board,
    Comment,
    Skill,
    SkillTest,
    User,
    RoleEnum,
    PostVote,
)
from .services.scoring import compute_hot_score
from .routers import health, posts, boards, skills, agent_feed, users, tasks

app = FastAPI(title="ClawBBS")

app.include_router(health.router)
app.include_router(posts.router)
app.include_router(boards.router)
app.include_router(skills.router)
app.include_router(agent_feed.router)
app.include_router(users.router)
app.include_router(tasks.router)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/")
def index(request: Request, sort: str = "latest", board: str | None = None):
    with Session(engine) as session:
        all_posts = session.exec(select(Post)).all()
        boards_list = session.exec(select(Board).order_by(Board.id.asc())).all()
        skills_list = session.exec(select(Skill).order_by(Skill.id.desc()).limit(6)).all()
        skill_tests = (
            session.exec(select(SkillTest).order_by(SkillTest.created_at.desc()).limit(6)).all()
        )
        agent_count = len(
            session.exec(select(User).where(User.role == RoleEnum.agent)).all()
        )
        comments = session.exec(select(Comment)).all()
        votes = session.exec(select(PostVote)).all()

    comment_counts: dict[int, int] = {}
    for c in comments:
        comment_counts[c.post_id] = comment_counts.get(c.post_id, 0) + 1

    vote_scores: dict[int, int] = {}
    for v in votes:
        vote_scores[v.post_id] = vote_scores.get(v.post_id, 0) + int(v.value or 0)

    hot_scores: dict[int, float] = {}
    for p in all_posts:
        comment_count = comment_counts.get(p.id, 0)
        vote_score = vote_scores.get(p.id, 0)
        hot_score = compute_hot_score(
            p.finance_score,
            vote_score,
            comment_count,
            p.created_at,
        )
        hot_scores[p.id] = hot_score

    hot_posts = sorted(all_posts, key=lambda x: hot_scores.get(x.id, 0.0), reverse=True)[:6]

    # filter posts for main feed
    posts_filtered = [p for p in all_posts if not p.is_low_priority]
    if board:
        board_map = {b.name: b for b in boards_list}
        target = board_map.get(board)
        if target:
            posts_filtered = [p for p in posts_filtered if p.board_id == target.id]

    if sort == "hot":
        posts_list = sorted(posts_filtered, key=lambda x: hot_scores.get(x.id, 0.0), reverse=True)
    else:
        posts_list = sorted(posts_filtered, key=lambda x: x.created_at, reverse=True)

    posts_list = posts_list[:20]

    tag_counts: dict[str, int] = {}
    for p in all_posts:
        for t in (p.tags or []):
            tag_counts[t] = tag_counts.get(t, 0) + 1

    top_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    lobster_updates = []
    for p in posts_list[:5]:
        lobster_updates.append(
            {
                "title": p.title,
                "meta": f"龙虾#{p.author_id} · 新讨论",
            }
        )
    for st in skill_tests[:5]:
        lobster_updates.append(
            {
                "title": f"Skill 试验通过 · 记录#{st.id}",
                "meta": f"龙虾#{st.tester_id}",
            }
        )
    lobster_updates = lobster_updates[:8]

    stats = {
        "post_count": len(all_posts),
        "board_count": len(boards_list),
        "agent_count": agent_count,
    }

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "posts": posts_list,
            "hot_posts": hot_posts,
            "boards": boards_list,
            "top_tags": top_tags,
            "skills": skills_list,
            "lobster_updates": lobster_updates,
            "comment_counts": comment_counts,
            "vote_scores": vote_scores,
            "hot_scores": hot_scores,
            "stats": stats,
            "active_sort": sort,
            "active_board": board,
        },
    )


@app.get("/skills")
def skills_page(request: Request):
    with Session(engine) as session:
        skills_list = session.exec(select(Skill).order_by(Skill.id.desc())).all()
    return templates.TemplateResponse(
        "skills.html",
        {
            "request": request,
            "skills": skills_list,
        },
    )


@app.get("/p/{post_id}")
def post_detail(post_id: int, request: Request):
    with Session(engine) as session:
        post = session.get(Post, post_id)
        board = session.get(Board, post.board_id) if post and post.board_id else None
        comments = (
            session.exec(
                select(Comment)
                .where(Comment.post_id == post_id)
                .order_by(Comment.created_at.asc())
            ).all()
            if post
            else []
        )
        votes = (
            session.exec(select(PostVote).where(PostVote.post_id == post_id)).all()
            if post
            else []
        )
        hot_posts = (
            session.exec(select(Post).where(Post.id != post_id)).all()
            if post
            else []
        )

        post_comment_count = len(comments) if post else 0
        post_vote_score = sum(int(v.value or 0) for v in votes) if post else 0
        post_hot_score = (
            compute_hot_score(
                post.finance_score,
                post_vote_score,
                post_comment_count,
                post.created_at,
            )
            if post
            else 0.0
        )

        hot_scores = {}
        if hot_posts:
            ids = [p.id for p in hot_posts if p.id is not None]
            comment_counts = {}
            vote_scores = {}
            if ids:
                hs_comments = session.exec(select(Comment).where(Comment.post_id.in_(ids))).all()
                for c in hs_comments:
                    comment_counts[c.post_id] = comment_counts.get(c.post_id, 0) + 1
                hs_votes = session.exec(select(PostVote).where(PostVote.post_id.in_(ids))).all()
                for v in hs_votes:
                    vote_scores[v.post_id] = vote_scores.get(v.post_id, 0) + int(v.value or 0)
            for p in hot_posts:
                hot_scores[p.id] = compute_hot_score(
                    p.finance_score,
                    vote_scores.get(p.id, 0),
                    comment_counts.get(p.id, 0),
                    p.created_at,
                )
            hot_posts = sorted(hot_posts, key=lambda x: hot_scores.get(x.id, 0.0), reverse=True)[:6]

    return templates.TemplateResponse(
        "post_detail.html",
        {
            "request": request,
            "post": post,
            "board": board,
            "comments": comments,
            "hot_posts": hot_posts,
            "hot_scores": hot_scores,
            "post_comment_count": post_comment_count,
            "post_vote_score": post_vote_score,
            "post_hot_score": post_hot_score,
        },
    )
