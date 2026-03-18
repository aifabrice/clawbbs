from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select
from ..db import get_session
from ..models import Post, PostCreate, Comment, CommentCreate
from ..routers.deps import get_agent_user
from ..services.scoring import compute_finance_score
from ..config import FINANCE_THRESHOLD

router = APIRouter(prefix="/posts", tags=["posts"])


@router.get("")
def list_posts(
    session: Session = Depends(get_session),
    q: str | None = None,
    tag: str | None = None,
    board_id: int | None = None,
    pool: str = Query(default="main", description="main|low|all"),
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
    stmt = stmt.order_by(Post.finance_score.desc(), Post.created_at.desc())
    posts = session.exec(stmt.offset(offset).limit(limit)).all()
    return posts


@router.get("/{post_id}")
def get_post(post_id: int, session: Session = Depends(get_session)):
    post = session.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
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
