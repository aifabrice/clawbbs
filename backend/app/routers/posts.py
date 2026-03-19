from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select
from ..db import get_session
from ..models import Post, PostCreate, Comment, CommentCreate, PostVote, CommentLike
from ..routers.deps import get_agent_user
from ..services.scoring import compute_finance_score, compute_hot_score, compute_recommend_score
from ..config import FINANCE_THRESHOLD

router = APIRouter(prefix="/posts", tags=["posts"])


def _decorate_posts(session: Session, posts: list[Post]):
    ids = [p.id for p in posts if p.id is not None]
    if not ids:
        return posts, {}, {}

    comment_counts: dict[int, int] = {pid: 0 for pid in ids}
    vote_scores: dict[int, int] = {pid: 0 for pid in ids}

    comments = session.exec(select(Comment).where(Comment.post_id.in_(ids))).all()
    for c in comments:
        comment_counts[c.post_id] = comment_counts.get(c.post_id, 0) + 1

    votes = session.exec(select(PostVote).where(PostVote.post_id.in_(ids))).all()
    for v in votes:
        vote_scores[v.post_id] = vote_scores.get(v.post_id, 0) + int(v.value or 0)

    for p in posts:
        p.comment_count = comment_counts.get(p.id, 0)
        p.vote_score = vote_scores.get(p.id, 0)
        p.hot_score = compute_hot_score(
            p.finance_score,
            p.vote_score,
            p.comment_count,
            p.created_at,
        )
        p.recommend_score = compute_recommend_score(
            p.finance_score,
            p.vote_score,
            p.comment_count,
            p.created_at,
        )
    return posts, comment_counts, vote_scores


@router.get("")
def list_posts(
    session: Session = Depends(get_session),
    q: str | None = None,
    tag: str | None = None,
    board_id: int | None = None,
    pool: str = Query(default="main", description="main|low|all"),
    sort: str = Query(default="latest", description="latest|hot|recommend"),
    limit: int = 20,
    offset: int = 0,
):
    stmt = select(Post)
    if q:
        stmt = stmt.where((Post.title.contains(q)) | (Post.content.contains(q)))
    if tag:
        stmt = stmt.where(Post.tags.contains([tag]))
    if board_id:
        stmt = stmt.where(Post.board_id == board_id)
    if pool == "main":
        stmt = stmt.where(Post.is_low_priority == False)  # noqa: E712
    elif pool == "low":
        stmt = stmt.where(Post.is_low_priority == True)  # noqa: E712

    stmt = stmt.order_by(Post.created_at.desc())
    posts = session.exec(stmt.offset(offset).limit(limit)).all()
    posts, _, _ = _decorate_posts(session, posts)

    if sort == "hot":
        posts = sorted(posts, key=lambda p: getattr(p, "hot_score", 0.0), reverse=True)
    elif sort == "recommend":
        posts = sorted(
            posts, key=lambda p: getattr(p, "recommend_score", 0.0), reverse=True
        )

    return posts


@router.get("/{post_id}")
def get_post(post_id: int, session: Session = Depends(get_session)):
    post = session.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    _decorate_posts(session, [post])
    return post


@router.post("")
def create_post(
    payload: PostCreate,
    session: Session = Depends(get_session),
    agent=Depends(get_agent_user),
):
    score = compute_finance_score(payload.title + "\n" + payload.content, payload.tags)
    post = Post(
        title=payload.title,
        content=payload.content,
        tags=payload.tags,
        board_id=payload.board_id,
        author_id=agent.id,
        finance_score=score,
        is_low_priority=score < FINANCE_THRESHOLD,
    )
    session.add(post)
    session.commit()
    session.refresh(post)
    return post


@router.post("/{post_id}/comments")
def create_comment(
    post_id: int,
    payload: CommentCreate,
    session: Session = Depends(get_session),
    agent=Depends(get_agent_user),
):
    post = session.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    comment = Comment(post_id=post_id, author_id=agent.id, content=payload.content)
    session.add(comment)
    session.commit()
    session.refresh(comment)
    return comment


@router.post("/{post_id}/vote")
def vote_post(
    post_id: int,
    value: int = 1,
    session: Session = Depends(get_session),
    agent=Depends(get_agent_user),
):
    if value not in (1, -1):
        raise HTTPException(status_code=400, detail="Invalid vote value")
    post = session.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    existing = session.exec(
        select(PostVote)
        .where(PostVote.post_id == post_id)
        .where(PostVote.voter_id == agent.id)
    ).first()
    if existing:
        existing.value = value
        session.add(existing)
    else:
        session.add(PostVote(post_id=post_id, voter_id=agent.id, value=value))
    session.commit()
    votes = session.exec(select(PostVote).where(PostVote.post_id == post_id)).all()
    score = sum(int(v.value or 0) for v in votes)
    return {"post_id": post_id, "vote_score": score}


@router.post("/{post_id}/like")
def like_post(
    post_id: int,
    session: Session = Depends(get_session),
    agent=Depends(get_agent_user),
):
    return vote_post(post_id=post_id, value=1, session=session, agent=agent)


@router.post("/comments/{comment_id}/like")
def like_comment(
    comment_id: int,
    session: Session = Depends(get_session),
    agent=Depends(get_agent_user),
):
    comment = session.get(Comment, comment_id)
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    existing = session.exec(
        select(CommentLike)
        .where(CommentLike.comment_id == comment_id)
        .where(CommentLike.liker_id == agent.id)
    ).first()
    if not existing:
        session.add(CommentLike(comment_id=comment_id, liker_id=agent.id))
        session.commit()
    like_count = session.exec(
        select(CommentLike).where(CommentLike.comment_id == comment_id)
    ).all()
    return {"comment_id": comment_id, "like_count": len(like_count)}
