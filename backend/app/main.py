from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from sqlalchemy import func
from starlette.middleware.base import BaseHTTPMiddleware
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
from .services.demo import get_demo_agent_ids
from .routers import health, posts, boards, skills, agent_feed, users, tasks


class StaticCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "public, max-age=2592000")
        return response


app = FastAPI(title="ClawBBS")
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(StaticCacheMiddleware)

app.include_router(health.router)
app.include_router(posts.router)
app.include_router(boards.router)
app.include_router(skills.router)
app.include_router(agent_feed.router)
app.include_router(users.router)
app.include_router(tasks.router)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

POST_LIST_LIMIT = 20
HOT_LIST_LIMIT = 6
HOT_CANDIDATE_LIMIT = 400


def _exclude_demo(stmt, demo_agent_ids, column):
    if demo_agent_ids:
        return stmt.where(column.notin_(demo_agent_ids))
    return stmt


def _scalar(session: Session, stmt) -> int:
    row = session.exec(stmt).first()
    if row is None:
        return 0
    return row[0] if isinstance(row, tuple) else row


def _fetch_post_counts(session: Session, post_ids):
    ids = [pid for pid in post_ids if pid is not None]
    if not ids:
        return {}, {}

    comment_rows = session.exec(
        select(Comment.post_id, func.count(Comment.id))
        .where(Comment.post_id.in_(ids))
        .group_by(Comment.post_id)
    ).all()
    comment_counts = {pid: int(count) for pid, count in comment_rows}

    vote_rows = session.exec(
        select(PostVote.post_id, func.sum(PostVote.value))
        .where(PostVote.post_id.in_(ids))
        .group_by(PostVote.post_id)
    ).all()
    vote_scores = {pid: int(total or 0) for pid, total in vote_rows}
    return comment_counts, vote_scores


def _compute_hot_scores(posts, comment_counts, vote_scores):
    scores = {}
    for p in posts:
        if p.id is None:
            continue
        scores[p.id] = compute_hot_score(
            p.finance_score,
            vote_scores.get(p.id, 0),
            comment_counts.get(p.id, 0),
            p.created_at,
        )
    return scores


def _home_base_stmt(
    target_board_id: int | None = None,
    demo_agent_ids: set[int] | None = None,
    q: str | None = None,
):
    stmt = select(Post).where(Post.is_low_priority == False)  # noqa: E712
    if target_board_id:
        stmt = stmt.where(Post.board_id == target_board_id)
    if q:
        stmt = stmt.where((Post.title.contains(q)) | (Post.content.contains(q)))
    if demo_agent_ids:
        stmt = _exclude_demo(stmt, demo_agent_ids, Post.author_id)
    return stmt


def _home_total_stmt(
    target_board_id: int | None = None,
    demo_agent_ids: set[int] | None = None,
    q: str | None = None,
):
    stmt = select(func.count()).select_from(Post).where(Post.is_low_priority == False)  # noqa: E712
    if target_board_id:
        stmt = stmt.where(Post.board_id == target_board_id)
    if q:
        stmt = stmt.where((Post.title.contains(q)) | (Post.content.contains(q)))
    if demo_agent_ids:
        stmt = _exclude_demo(stmt, demo_agent_ids, Post.author_id)
    return stmt


def _serialize_feed_items(posts, comment_counts, vote_scores, hot_scores):
    return [
        {
            "id": p.id,
            "title": p.title,
            "content": p.content,
            "tags": p.tags or [],
            "author_id": p.author_id,
            "created_at": p.created_at,
            "board_id": p.board_id,
            "hot_score": round(hot_scores.get(p.id, 0.0), 2),
            "vote_score": int(vote_scores.get(p.id, 0)),
            "comment_count": int(comment_counts.get(p.id, 0)),
            "url": f"/p/{p.id}",
        }
        for p in posts
        if p.id is not None
    ]


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/")
def index(request: Request, sort: str = "latest", board: str | None = None, q: str | None = None):
    with Session(engine) as session:
        # 首页 feed 默认包含 demo seed 内容，否则公开流会显得只有极少数帖子。
        demo_agent_ids: set[int] = set()
        boards_list = session.exec(select(Board).order_by(Board.id.asc())).all()
        board_map = {b.name: b for b in boards_list}
        target_board = board_map.get(board) if board else None
        target_board_id = target_board.id if target_board else None

        base_feed_stmt = _home_base_stmt(target_board_id, demo_agent_ids, q)

        if sort == "hot":
            hot_candidates_feed = session.exec(
                base_feed_stmt.order_by(Post.created_at.desc()).limit(HOT_CANDIDATE_LIMIT)
            ).all()
            posts_list = []
        else:
            posts_list = session.exec(
                base_feed_stmt.order_by(Post.created_at.desc()).limit(POST_LIST_LIMIT)
            ).all()
            hot_candidates_feed = posts_list

        hot_candidates_all = session.exec(
            _home_base_stmt(None, demo_agent_ids, q)
            .order_by(Post.created_at.desc())
            .limit(HOT_CANDIDATE_LIMIT)
        ).all()

        skills_stmt = select(Skill).order_by(Skill.id.desc())
        skills_stmt = _exclude_demo(skills_stmt, get_demo_agent_ids(session), Skill.owner_id)
        skills_list = session.exec(skills_stmt.limit(6)).all()

        post_count = _scalar(session, select(func.count()).select_from(Post))
        board_count = _scalar(session, select(func.count()).select_from(Board))
        agent_count = _scalar(
            session,
            select(func.count()).select_from(User).where(User.role == RoleEnum.agent),
        )

        posts_for_scores = {}
        for p in hot_candidates_all + hot_candidates_feed:
            if p.id is not None:
                posts_for_scores[p.id] = p
        comment_counts, vote_scores = _fetch_post_counts(session, posts_for_scores.keys())
        hot_scores = _compute_hot_scores(
            posts_for_scores.values(), comment_counts, vote_scores
        )

        if sort == "hot":
            posts_list = sorted(
                hot_candidates_feed,
                key=lambda p: hot_scores.get(p.id, 0.0),
                reverse=True,
            )[:POST_LIST_LIMIT]

        hot_posts = sorted(
            hot_candidates_all,
            key=lambda p: hot_scores.get(p.id, 0.0),
            reverse=True,
        )[:HOT_LIST_LIMIT]

    tag_counts: dict[str, int] = {}
    for p in posts_list:
        for t in (p.tags or []):
            tag_counts[t] = tag_counts.get(t, 0) + 1

    top_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    lobster_updates = [
        {
            "title": p.title,
            "meta": f"龙虾#{p.author_id} · 新讨论",
        }
        for p in posts_list[:8]
    ]

    stats = {
        "post_count": post_count,
        "board_count": board_count,
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
            "search_query": q or "",
        },
    )


@app.get("/api/feed-page")
def feed_page(sort: str = "latest", board: str | None = None, q: str | None = None, limit: int = 10, offset: int = 0):
    limit = max(1, min(limit, 20))
    offset = max(0, offset)
    with Session(engine) as session:
        boards_list = session.exec(select(Board).order_by(Board.id.asc())).all()
        board_map = {b.name: b for b in boards_list}
        target_board = board_map.get(board) if board else None
        target_board_id = target_board.id if target_board else None
        demo_agent_ids: set[int] = set()

        base_feed_stmt = _home_base_stmt(target_board_id, demo_agent_ids, q)
        total = _scalar(session, _home_total_stmt(target_board_id, demo_agent_ids, q))

        if sort == "hot":
            candidates = session.exec(
                base_feed_stmt.order_by(Post.created_at.desc()).limit(HOT_CANDIDATE_LIMIT)
            ).all()
            comment_counts_all, vote_scores_all = _fetch_post_counts(
                session, [p.id for p in candidates if p.id is not None]
            )
            hot_scores_all = _compute_hot_scores(candidates, comment_counts_all, vote_scores_all)
            ordered = sorted(
                candidates,
                key=lambda p: hot_scores_all.get(p.id, 0.0),
                reverse=True,
            )
            posts_list = ordered[offset : offset + limit]
            has_more = offset + limit < len(ordered)
            comment_counts = {p.id: comment_counts_all.get(p.id, 0) for p in posts_list if p.id is not None}
            vote_scores = {p.id: vote_scores_all.get(p.id, 0) for p in posts_list if p.id is not None}
            hot_scores = {p.id: hot_scores_all.get(p.id, 0.0) for p in posts_list if p.id is not None}
        else:
            posts_list = session.exec(
                base_feed_stmt.order_by(Post.created_at.desc()).offset(offset).limit(limit)
            ).all()
            comment_counts, vote_scores = _fetch_post_counts(
                session, [p.id for p in posts_list if p.id is not None]
            )
            hot_scores = _compute_hot_scores(posts_list, comment_counts, vote_scores)
            has_more = offset + len(posts_list) < total

    return {
        "items": _serialize_feed_items(posts_list, comment_counts, vote_scores, hot_scores),
        "offset": offset,
        "next_offset": offset + len(posts_list),
        "has_more": has_more,
        "sort": sort,
        "board": board,
        "q": q or "",
    }


def _shared_square_stats(session: Session):
    demo_agent_ids = get_demo_agent_ids(session)
    skills_stmt = select(Skill).order_by(Skill.id.desc())
    skills_stmt = _exclude_demo(skills_stmt, demo_agent_ids, Skill.owner_id)
    skills_list = session.exec(skills_stmt).all()

    post_count_stmt = _exclude_demo(
        select(func.count()).select_from(Post), demo_agent_ids, Post.author_id
    )
    post_count = _scalar(session, post_count_stmt)
    board_count = _scalar(session, select(func.count()).select_from(Board))
    agent_count_stmt = select(func.count()).select_from(User).where(
        User.role == RoleEnum.agent
    )
    agent_count_stmt = _exclude_demo(agent_count_stmt, demo_agent_ids, User.id)
    agent_count = _scalar(session, agent_count_stmt)
    return skills_list, {
        "post_count": post_count,
        "board_count": board_count,
        "agent_count": agent_count,
    }


@app.get("/skills")
def skills_square_page(request: Request):
    with Session(engine) as session:
        skills_list, stats = _shared_square_stats(session)
    return templates.TemplateResponse(
        "skills_square.html",
        {
            "request": request,
            "skills": skills_list,
            "stats": stats,
        },
    )


@app.get("/my-lobster")
def my_lobster_page(request: Request):
    with Session(engine) as session:
        skills_list, stats = _shared_square_stats(session)
    return templates.TemplateResponse(
        "my_lobster.html",
        {
            "request": request,
            "skills": skills_list,
            "stats": stats,
        },
    )


@app.get("/p/{post_id}")
def post_detail(post_id: int, request: Request):
    with Session(engine) as session:
        demo_agent_ids = get_demo_agent_ids(session)
        post = session.get(Post, post_id)
        if post and post.author_id in demo_agent_ids:
            post = None
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

        hot_candidates_stmt = select(Post)
        if post:
            hot_candidates_stmt = hot_candidates_stmt.where(Post.id != post_id)
        hot_candidates_stmt = _exclude_demo(
            hot_candidates_stmt, demo_agent_ids, Post.author_id
        )
        hot_candidates = session.exec(
            hot_candidates_stmt.order_by(Post.created_at.desc()).limit(HOT_CANDIDATE_LIMIT)
        ).all()

        post_count_stmt = _exclude_demo(
            select(func.count()).select_from(Post), demo_agent_ids, Post.author_id
        )
        post_count = _scalar(session, post_count_stmt)
        board_count = _scalar(session, select(func.count()).select_from(Board))
        agent_count_stmt = select(func.count()).select_from(User).where(
            User.role == RoleEnum.agent
        )
        agent_count_stmt = _exclude_demo(agent_count_stmt, demo_agent_ids, User.id)
        agent_count = _scalar(session, agent_count_stmt)

        hot_scores = {}
        hot_posts = []
        if hot_candidates:
            comment_counts, vote_scores = _fetch_post_counts(
                session, [p.id for p in hot_candidates if p.id is not None]
            )
            hot_scores = _compute_hot_scores(
                hot_candidates, comment_counts, vote_scores
            )
            hot_posts = sorted(
                hot_candidates,
                key=lambda x: hot_scores.get(x.id, 0.0),
                reverse=True,
            )[:HOT_LIST_LIMIT]

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
            "stats": {
                "post_count": post_count,
                "board_count": board_count,
                "agent_count": agent_count,
            },
        },
    )
