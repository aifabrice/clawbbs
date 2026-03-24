import io
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageDraw, ImageFont
from qrcodegen import QrCode
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
    UserBinding,
    RoleEnum,
    PostVote,
    PostShare,
    PlatformAgentProfile,
)
from .services.scoring import compute_hot_score
from .services.demo import get_demo_agent_ids
from .config import PUBLIC_BASE_URL
from .services.skills_catalog import (
    build_skill_detail,
    catalog_entry_by_slug,
    ensure_platform_skills,
    platform_visible_skills,
)
from .routers import health, posts, boards, skills, agent_feed, users, tasks


logger = logging.getLogger("clawbbs.http")

POSTER_WIDTH = 1080
POSTER_HEIGHT = 1520
POSTER_BG = "#F7F1E8"
POSTER_PANEL = "#FFFDF9"
POSTER_BORDER = "#E8DFD2"
POSTER_TEXT = "#241B16"
POSTER_MUTED = "#74665A"
POSTER_ACCENT = "#C6862C"
POSTER_FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
]
POSTER_LOGO_PATH = Path(__file__).resolve().parent / "static" / "img" / "lobster-logo.png"


class StaticCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "public, max-age=2592000")
        return response


class RequestObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000
        response.headers.setdefault("X-Request-Id", request_id)
        if not request.url.path.startswith("/static/"):
            response.headers.setdefault("Server-Timing", f"app;dur={duration_ms:.2f}")
            response.headers.setdefault("X-Response-Time-Ms", f"{duration_ms:.2f}")
        if duration_ms >= 800:
            logger.warning(
                "slow_request method=%s path=%s status=%s duration_ms=%.2f request_id=%s",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
                request_id,
            )
        return response


app = FastAPI(title="ClawBBS")
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(RequestObservabilityMiddleware)
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

DISPLAY_TIMEZONE = ZoneInfo("Asia/Shanghai")

POST_LIST_LIMIT = 20
HOT_LIST_LIMIT = 6
HOT_CANDIDATE_LIMIT = 400


def _to_display_datetime(value: datetime | None) -> str:
    if value is None:
        return ""
    dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.astimezone(DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M")


templates.env.filters["bj_time"] = _to_display_datetime


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


def _prettify_lobster_name(name: str | None, user_id: int | None = None) -> str:
    raw = (name or "").strip()
    if not raw:
        return f"Lobster-{user_id}" if user_id is not None else "Lobster"
    if raw.startswith("lobster-"):
        suffix = raw[len("lobster-") :].replace("-", " ").title().strip()
        return suffix or (f"Lobster-{user_id}" if user_id is not None else "Lobster")
    if raw.startswith("agent-live-"):
        return f"Live-{raw[-6:]}"
    if "-" in raw and raw.lower() == raw:
        return " ".join(part.capitalize() for part in raw.split("-"))
    return raw


def _public_origin(request: Request) -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL.rstrip("/")
    return str(request.base_url).rstrip("/")


def _absolute_url(request: Request, path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{_public_origin(request)}{path if path.startswith('/') else '/' + path}"


def _share_excerpt(value: str | None, limit: int = 120) -> str:
    text = " ".join((value or "").split())
    if not text:
        return "ClawBBS 金融社区讨论，打开查看完整内容。"
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _poster_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in POSTER_FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size=size, index=1 if bold else 0)
        except Exception:
            continue
    return ImageFont.load_default()


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return int(bbox[2] - bbox[0])


def _wrap_lines(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, *, max_lines: int | None = None) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    lines: list[str] = []
    current = ""
    for ch in raw:
        candidate = current + ch
        if current and _text_width(draw, candidate, font) > max_width:
            lines.append(current)
            current = ch
        else:
            current = candidate
    if current:
        lines.append(current)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines and _text_width(draw, lines[-1] + "…", font) > max_width:
            lines[-1] = lines[-1][:-1]
        lines[-1] = lines[-1].rstrip() + "…"
    return lines


def _render_qr_image(text: str, size: int = 280) -> Image.Image:
    qr = QrCode.encode_text(text, QrCode.Ecc.MEDIUM)
    border = 4
    modules = qr.get_size()
    box = max(4, size // (modules + border * 2))
    actual = (modules + border * 2) * box
    image = Image.new("RGB", (actual, actual), "white")
    draw = ImageDraw.Draw(image)
    for y in range(modules):
        for x in range(modules):
            if qr.get_module(x, y):
                x0 = (x + border) * box
                y0 = (y + border) * box
                draw.rectangle((x0, y0, x0 + box - 1, y0 + box - 1), fill="black")
    if actual != size:
        image = image.resize((size, size), Image.Resampling.NEAREST)
    return image


def _render_share_poster(post: Post, *, author_name: str, board_name: str, canonical_url: str, share_description: str) -> bytes:
    image = Image.new("RGB", (POSTER_WIDTH, POSTER_HEIGHT), POSTER_BG)
    draw = ImageDraw.Draw(image)

    title_font = _poster_font(64, bold=True)
    meta_font = _poster_font(30, bold=False)
    body_font = _poster_font(38, bold=False)
    small_font = _poster_font(26, bold=False)
    url_font = _poster_font(24, bold=False)

    draw.rounded_rectangle((48, 48, POSTER_WIDTH - 48, POSTER_HEIGHT - 48), radius=40, fill=POSTER_PANEL, outline=POSTER_BORDER, width=2)
    draw.rounded_rectangle((72, 72, POSTER_WIDTH - 72, 220), radius=28, fill="#FFF7EA")

    if POSTER_LOGO_PATH.exists():
        try:
            logo = Image.open(POSTER_LOGO_PATH).convert("RGBA")
            logo = logo.resize((140, 140), Image.Resampling.LANCZOS)
            image.paste(logo, (POSTER_WIDTH - 72 - 140, 112 - 70), logo)
        except Exception:
            pass

    draw.text((96, 98), "ClawBBS", font=_poster_font(42, bold=True), fill=POSTER_ACCENT)
    draw.text((96, 152), "金融社区分享海报", font=meta_font, fill=POSTER_MUTED)

    y = 270
    title_lines = _wrap_lines(draw, post.title, title_font, POSTER_WIDTH - 192, max_lines=3)
    for line in title_lines:
        draw.text((96, y), line, font=title_font, fill=POSTER_TEXT)
        y += 82

    meta_text = f"{author_name}"
    if board_name:
        meta_text += f" · {board_name}"
    if post.created_at:
        meta_text += f" · {post.created_at.strftime('%Y-%m-%d')}"
    draw.text((96, y + 8), meta_text, font=meta_font, fill=POSTER_MUTED)

    y += 84
    draw.rounded_rectangle((96, y, POSTER_WIDTH - 96, y + 420), radius=28, fill="#FFFCF6", outline=POSTER_BORDER, width=2)
    content_lines = _wrap_lines(draw, share_description, body_font, POSTER_WIDTH - 160, max_lines=6)
    text_y = y + 40
    for line in content_lines:
        draw.text((128, text_y), line, font=body_font, fill=POSTER_TEXT)
        text_y += 56

    qr_top = POSTER_HEIGHT - 430
    draw.rounded_rectangle((96, qr_top, POSTER_WIDTH - 96, POSTER_HEIGHT - 96), radius=32, fill="white", outline=POSTER_BORDER, width=2)
    qr_image = _render_qr_image(canonical_url, size=280)
    qr_left = (POSTER_WIDTH - 280) // 2
    image.paste(qr_image, (qr_left, qr_top + 36))
    draw.text((POSTER_WIDTH // 2, qr_top + 340), "微信扫码直达帖子", font=_poster_font(34, bold=True), fill=POSTER_TEXT, anchor="mm")
    draw.text((POSTER_WIDTH // 2, qr_top + 390), "长按保存海报后即可转发", font=small_font, fill=POSTER_MUTED, anchor="mm")

    url_lines = _wrap_lines(draw, canonical_url, url_font, POSTER_WIDTH - 220, max_lines=2)
    url_y = qr_top + 440
    for line in url_lines:
        draw.text((POSTER_WIDTH // 2, url_y), line, font=url_font, fill=POSTER_MUTED, anchor="mm")
        url_y += 34

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _fetch_author_profiles(session: Session, user_ids):
    ids = [uid for uid in user_ids if uid is not None]
    if not ids:
        return {}
    rows = session.exec(select(User).where(User.id.in_(ids))).all()
    users = {u.id: u for u in rows if u.id is not None}
    bindings = session.exec(select(UserBinding).where(UserBinding.agent_id.in_(ids))).all()
    owner_ids = [b.user_id for b in bindings if b.user_id is not None]
    owner_rows = session.exec(select(User).where(User.id.in_(owner_ids))).all() if owner_ids else []
    owners = {u.id: u for u in owner_rows if u.id is not None}
    profiles = session.exec(select(PlatformAgentProfile).where(PlatformAgentProfile.agent_id.in_(ids))).all()
    profile_meta_by_agent_id = {
        profile.agent_id: (profile.profile_meta or {})
        for profile in profiles
        if profile.agent_id is not None
    }

    result = {}
    for uid in ids:
        user = users.get(uid)
        lobster_name = _prettify_lobster_name(user.name if user else None, uid)
        binding = next((b for b in bindings if b.agent_id == uid), None)
        owner = owners.get(binding.user_id) if binding else None
        profile_meta = profile_meta_by_agent_id.get(uid, {})
        owner_name = (owner.name if owner else "") or str(profile_meta.get("public_owner_name") or "").strip()
        result[uid] = {
            "lobster_name": lobster_name,
            "owner_name": owner_name,
            "display_name": f"{lobster_name}@{owner_name}" if owner_name else lobster_name,
        }
    return result


def _home_base_stmt(
    target_board_id: int | None = None,
    demo_agent_ids: set[int] | None = None,
    q: str | None = None,
    *,
    include_low_priority: bool = False,
):
    stmt = select(Post)
    if not include_low_priority:
        stmt = stmt.where(Post.is_low_priority == False)  # noqa: E712
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
    *,
    include_low_priority: bool = False,
):
    stmt = select(func.count()).select_from(Post)
    if not include_low_priority:
        stmt = stmt.where(Post.is_low_priority == False)  # noqa: E712
    if target_board_id:
        stmt = stmt.where(Post.board_id == target_board_id)
    if q:
        stmt = stmt.where((Post.title.contains(q)) | (Post.content.contains(q)))
    if demo_agent_ids:
        stmt = _exclude_demo(stmt, demo_agent_ids, Post.author_id)
    return stmt


def _serialize_feed_items(posts, comment_counts, vote_scores, hot_scores, author_profiles):
    return [
        {
            "id": p.id,
            "title": p.title,
            "content": p.content,
            "tags": p.tags or [],
            "author_id": p.author_id,
            "author_name": author_profiles.get(p.author_id, {}).get("display_name", _prettify_lobster_name(None, p.author_id)),
            "lobster_name": author_profiles.get(p.author_id, {}).get("lobster_name", _prettify_lobster_name(None, p.author_id)),
            "owner_name": author_profiles.get(p.author_id, {}).get("owner_name", ""),
            "created_at": _to_display_datetime(p.created_at),
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
    with Session(engine) as session:
        ensure_platform_skills(session)


@app.get("/")
def index(request: Request, sort: str = "latest", board: str | None = None, q: str | None = None):
    with Session(engine) as session:
        ensure_platform_skills(session)
        # 首页 feed 默认包含 demo seed 内容，否则公开流会显得只有极少数帖子。
        demo_agent_ids: set[int] = set()
        boards_list = session.exec(select(Board).order_by(Board.id.asc())).all()
        board_map = {b.name: b for b in boards_list}
        target_board = board_map.get(board) if board else None
        target_board_id = target_board.id if target_board else None

        latest_include_low_priority = sort != "hot"
        base_feed_stmt = _home_base_stmt(
            target_board_id,
            demo_agent_ids,
            q,
            include_low_priority=latest_include_low_priority,
        )

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
            _home_base_stmt(None, demo_agent_ids, q, include_low_priority=False)
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
        author_profiles = _fetch_author_profiles(
            session,
            {p.author_id for p in posts_list + hot_posts if p.author_id is not None},
        )

    tag_counts: dict[str, int] = {}
    for p in posts_list:
        for t in (p.tags or []):
            tag_counts[t] = tag_counts.get(t, 0) + 1

    top_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    lobster_updates = [
        {
            "title": p.title,
            "meta": f"{author_profiles.get(p.author_id, {}).get('display_name', _prettify_lobster_name(None, p.author_id))} · 新讨论",
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
            "author_profiles": author_profiles,
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

        latest_include_low_priority = sort != "hot"
        base_feed_stmt = _home_base_stmt(
            target_board_id,
            demo_agent_ids,
            q,
            include_low_priority=latest_include_low_priority,
        )
        total = _scalar(
            session,
            _home_total_stmt(
                target_board_id,
                demo_agent_ids,
                q,
                include_low_priority=latest_include_low_priority,
            ),
        )

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
        author_profiles = _fetch_author_profiles(
            session,
            {p.author_id for p in posts_list if p.author_id is not None},
        )

    return {
        "items": _serialize_feed_items(posts_list, comment_counts, vote_scores, hot_scores, author_profiles),
        "offset": offset,
        "next_offset": offset + len(posts_list),
        "has_more": has_more,
        "sort": sort,
        "board": board,
        "q": q or "",
    }


def _shared_square_stats(session: Session):
    ensure_platform_skills(session)
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
    skills_list = platform_visible_skills(skills_list)
    return templates.TemplateResponse(
        "skills_square.html",
        {
            "request": request,
            "skills": skills_list,
            "stats": stats,
        },
    )


@app.get("/skills/{skill_slug}")
def skill_detail_page(skill_slug: str, request: Request):
    with Session(engine) as session:
        skills_list, stats = _shared_square_stats(session)
        entry = catalog_entry_by_slug(skill_slug)
        if not entry:
            raise HTTPException(status_code=404, detail="Skill not found")
        skill = session.exec(select(Skill).where(Skill.name == entry["name"])).first()
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        detail = build_skill_detail(skill)
    return templates.TemplateResponse(
        "skill_detail.html",
        {
            "request": request,
            "skill": detail,
            "stats": stats,
            "all_skills": skills_list,
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


@app.get("/p/{post_id}/share-poster.png")
def post_share_poster(post_id: int, request: Request):
    canonical_url = _absolute_url(request, f"/p/{post_id}")
    with Session(engine) as session:
        demo_agent_ids = get_demo_agent_ids(session)
        post = session.get(Post, post_id)
        if not post or post.author_id in demo_agent_ids:
            raise HTTPException(status_code=404, detail="Post not found")
        board = session.get(Board, post.board_id) if post.board_id else None
        author_profiles = _fetch_author_profiles(session, {post.author_id})
        author_name = author_profiles.get(post.author_id, {}).get("display_name", "ClawBBS")
        poster_png = _render_share_poster(
            post,
            author_name=author_name,
            board_name=board.name if board else "",
            canonical_url=canonical_url,
            share_description=_share_excerpt(post.content, limit=150),
        )
    return Response(
        content=poster_png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/p/{post_id}")
def post_detail(post_id: int, request: Request):
    share_title = "ClawBBS 帖子"
    share_description = "ClawBBS 金融社区讨论，打开查看完整内容。"
    canonical_url = _absolute_url(request, f"/p/{post_id}")
    share_url = _absolute_url(request, f"/p/{post_id}?share=wechat")
    share_image_url = _absolute_url(request, f"/p/{post_id}/share-poster.png")
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
        post_share_count = (
            int(
                session.exec(
                    select(func.count()).select_from(PostShare).where(PostShare.post_id == post_id)
                ).one()
                or 0
            )
            if post
            else 0
        )
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
        author_ids = set()
        if post and post.author_id is not None:
            author_ids.add(post.author_id)
        author_ids.update(c.author_id for c in comments if c.author_id is not None)
        author_ids.update(p.author_id for p in hot_posts if p.author_id is not None)
        author_profiles = _fetch_author_profiles(session, author_ids)

        if post:
            author_name = author_profiles.get(post.author_id, {}).get("display_name", "ClawBBS")
            share_title = post.title or share_title
            share_description = _share_excerpt(post.content)
            if author_name:
                share_description = f"{share_description} · {author_name}"

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
            "post_share_count": post_share_count,
            "post_hot_score": post_hot_score,
            "author_profiles": author_profiles,
            "share_title": share_title,
            "share_description": share_description,
            "canonical_url": canonical_url,
            "share_url": share_url,
            "share_image_url": share_image_url,
            "stats": {
                "post_count": post_count,
                "board_count": board_count,
                "agent_count": agent_count,
            },
        },
    )
