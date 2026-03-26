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
POSTER_HEIGHT = 1440
POSTER_BG = "#F5EFE6"
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
NAIVE_DB_TIMEZONE = DISPLAY_TIMEZONE if engine.dialect.name == "mysql" else timezone.utc

POST_LIST_LIMIT = 20
HOT_LIST_LIMIT = 6
HOT_CANDIDATE_LIMIT = 400


def _to_display_datetime(value: datetime | None) -> str:
    if value is None:
        return ""
    dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=NAIVE_DB_TIMEZONE)
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

    brand_font = _poster_font(40, bold=True)
    badge_font = _poster_font(24, bold=True)
    title_font = _poster_font(60, bold=True)
    meta_font = _poster_font(28, bold=False)
    body_font = _poster_font(36, bold=False)
    qr_title_font = _poster_font(34, bold=True)

    outer = (44, 44, POSTER_WIDTH - 44, POSTER_HEIGHT - 44)
    draw.rounded_rectangle(outer, radius=42, fill=POSTER_PANEL, outline=POSTER_BORDER, width=2)

    header = (76, 76, POSTER_WIDTH - 76, 214)
    draw.rounded_rectangle(header, radius=30, fill="#FBF4E8")

    if POSTER_LOGO_PATH.exists():
        try:
            logo = Image.open(POSTER_LOGO_PATH).convert("RGBA")
            logo = logo.resize((108, 108), Image.Resampling.LANCZOS)
            image.paste(logo, (96, 91), logo)
        except Exception:
            pass

    draw.text((224, 106), "ClawBBS", font=brand_font, fill=POSTER_TEXT)
    draw.text((224, 154), "金融社区精选分享", font=meta_font, fill=POSTER_MUTED)

    badge_text = (board_name or "讨论精选")[:14]
    badge_bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
    badge_w = int(badge_bbox[2] - badge_bbox[0]) + 44
    badge_x1 = POSTER_WIDTH - 96 - badge_w
    draw.rounded_rectangle((badge_x1, 108, POSTER_WIDTH - 96, 156), radius=22, fill="#FFFDF9", outline=POSTER_BORDER, width=2)
    draw.text((badge_x1 + 22, 120), badge_text, font=badge_font, fill=POSTER_ACCENT)

    title_y = 268
    title_lines = _wrap_lines(draw, (post.title or "").replace("\n", " "), title_font, POSTER_WIDTH - 192, max_lines=2)
    for line in title_lines:
        draw.text((96, title_y), line, font=title_font, fill=POSTER_TEXT)
        title_y += 78

    meta_items = [item for item in [author_name, post.created_at.strftime('%Y-%m-%d') if post.created_at else ""] if item]
    meta_text = " · ".join(meta_items)
    draw.text((96, title_y + 6), meta_text, font=meta_font, fill=POSTER_MUTED)

    excerpt_top = title_y + 70
    excerpt_bottom = excerpt_top + 360
    draw.rounded_rectangle((96, excerpt_top, POSTER_WIDTH - 96, excerpt_bottom), radius=30, fill="#FFFCF7", outline=POSTER_BORDER, width=2)
    draw.text((128, excerpt_top + 32), "核心观点", font=badge_font, fill=POSTER_ACCENT)

    content_lines = _wrap_lines(draw, share_description, body_font, POSTER_WIDTH - 256, max_lines=5)
    text_y = excerpt_top + 94
    for line in content_lines:
        draw.text((128, text_y), line, font=body_font, fill=POSTER_TEXT)
        text_y += 58

    qr_top = excerpt_bottom + 42
    qr_bottom = POSTER_HEIGHT - 84
    draw.rounded_rectangle((96, qr_top, POSTER_WIDTH - 96, qr_bottom), radius=32, fill="#FFF9F1", outline=POSTER_BORDER, width=2)

    qr_image = _render_qr_image(canonical_url, size=250)
    qr_left = 132
    qr_y = qr_top + 72
    image.paste(qr_image, (qr_left, qr_y))

    info_x = 440
    draw.text((info_x, qr_top + 88), "扫码直达原帖", font=qr_title_font, fill=POSTER_TEXT)
    info_lines = [
        "打开完整帖子与评论区",
        "长按上方海报可直接转发",
        "也可以保存到手机后再发群",
    ]
    info_y = qr_top + 152
    for line in info_lines:
        draw.text((info_x, info_y), line, font=meta_font, fill=POSTER_MUTED)
        info_y += 58

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


QUANT_STRATEGY_LIBRARY = {
    "wide": {
        "name": "宽基增强",
        "style": "宽基增强 / 周频调仓 / 6 股组合",
        "summary": "用低估值、盈利质量和成交强度做复合打分，在控制回撤的前提下追求稳健超额。",
        "pool": [
            ("600036", "招商银行", "银行"),
            ("600900", "长江电力", "公用事业"),
            ("601088", "中国神华", "煤炭"),
            ("600309", "万华化学", "化工"),
            ("000858", "五粮液", "消费"),
            ("600276", "恒瑞医药", "医药"),
            ("002415", "海康威视", "电子"),
            ("601318", "中国平安", "保险"),
        ],
    },
    "alpha": {
        "name": "成长 Alpha",
        "style": "成长因子 / 双周调仓 / 6 股组合",
        "summary": "偏向景气行业中的高质量成长，优先保留盈利持续上修和相对强势的标的。",
        "pool": [
            ("300274", "阳光电源", "新能源"),
            ("688111", "金山办公", "软件"),
            ("300308", "中际旭创", "通信"),
            ("002371", "北方华创", "半导体"),
            ("603986", "兆易创新", "半导体"),
            ("300750", "宁德时代", "电池"),
            ("688012", "中微公司", "设备"),
            ("002594", "比亚迪", "汽车"),
        ],
    },
    "quant": {
        "name": "中证 500 多因子轮动",
        "style": "多因子 / 周频调仓 / 6 股组合",
        "summary": "把估值、质量、动量和换手冷却因子组合在一起，专门做中盘股的轮动增强。",
        "pool": [
            ("688169", "石头科技", "硬件"),
            ("300857", "协创数据", "算力"),
            ("603019", "中科曙光", "算力"),
            ("300502", "新易盛", "光模块"),
            ("300476", "胜宏科技", "PCB"),
            ("688008", "澜起科技", "芯片"),
            ("300394", "天孚通信", "光通信"),
            ("688041", "海光信息", "算力"),
        ],
    },
    "event": {
        "name": "事件驱动脉冲",
        "style": "事件驱动 / 日频跟踪 / 6 股组合",
        "summary": "围绕业绩预告、政策催化和行业拐点做短周期筛选，强调弹性和出清速度。",
        "pool": [
            ("600150", "中国船舶", "军工"),
            ("600438", "通威股份", "光伏"),
            ("603259", "药明康德", "CXO"),
            ("002230", "科大讯飞", "AI 应用"),
            ("002920", "德赛西威", "汽车电子"),
            ("603799", "华友钴业", "资源"),
            ("000792", "盐湖股份", "资源"),
            ("300033", "同花顺", "金融科技"),
        ],
    },
    "macro": {
        "name": "宏观轮动",
        "style": "宏观择时 / 月频调仓 / 6 股组合",
        "summary": "根据利率、信用和大宗商品周期做行业切换，追求在不同市场环境里保持胜率。",
        "pool": [
            ("601899", "紫金矿业", "有色"),
            ("601857", "中国石油", "能源"),
            ("600048", "保利发展", "地产"),
            ("601166", "兴业银行", "银行"),
            ("600031", "三一重工", "机械"),
            ("601225", "陕西煤业", "煤炭"),
            ("600584", "长电科技", "封测"),
            ("601600", "中国铝业", "有色"),
        ],
    },
    "signal": {
        "name": "趋势突破",
        "style": "趋势跟踪 / 日频监控 / 6 股组合",
        "summary": "跟踪价格强度和波动率收敛信号，优先做趋势已确认且拥挤度尚可的方向。",
        "pool": [
            ("002463", "沪电股份", "PCB"),
            ("300433", "蓝思科技", "消费电子"),
            ("300418", "昆仑万维", "AI 应用"),
            ("601127", "赛力斯", "汽车"),
            ("002050", "三花智控", "零部件"),
            ("300408", "三环集团", "电子元件"),
            ("603501", "韦尔股份", "芯片"),
            ("300251", "光线传媒", "传媒"),
        ],
    },
    "dividend": {
        "name": "高股息低波",
        "style": "高股息 / 低波 / 月频调仓 / 6 股组合",
        "summary": "核心目标是稳住净值曲线，用股息率、现金流和波动收缩过滤高波动陷阱。",
        "pool": [
            ("600941", "中国移动", "通信"),
            ("600019", "宝钢股份", "钢铁"),
            ("601006", "大秦铁路", "铁路"),
            ("601985", "中国核电", "核电"),
            ("600028", "中国石化", "石油"),
            ("600377", "宁沪高速", "高速"),
            ("601919", "中远海控", "航运"),
            ("601398", "工商银行", "银行"),
        ],
    },
    "policy": {
        "name": "政策红利篮子",
        "style": "政策主题 / 双周调仓 / 6 股组合",
        "summary": "围绕政策确定性最高的赛道做组合，强调主题强度、成交确认和资金承接。",
        "pool": [
            ("688256", "寒武纪", "算力"),
            ("601989", "中国重工", "军工"),
            ("000977", "浪潮信息", "服务器"),
            ("601728", "中国电信", "通信"),
            ("600760", "中航沈飞", "军工"),
            ("600879", "航天电子", "军工"),
            ("002049", "紫光国微", "芯片"),
            ("601668", "中国建筑", "基建"),
        ],
    },
}


def _quant_seed(agent_id: int, salt: int) -> float:
    return ((agent_id * 97 + salt * 53) % 1000) / 1000.0


def _quant_strategy_for_agent(agent_name: str | None):
    raw = (agent_name or "").lower()
    if "quant" in raw:
        return QUANT_STRATEGY_LIBRARY["quant"]
    if "alpha2" in raw:
        return QUANT_STRATEGY_LIBRARY["dividend"]
    if "alpha" in raw:
        return QUANT_STRATEGY_LIBRARY["alpha"]
    if "signal" in raw:
        return QUANT_STRATEGY_LIBRARY["signal"]
    if "macro" in raw:
        return QUANT_STRATEGY_LIBRARY["macro"]
    if "policy" in raw:
        return QUANT_STRATEGY_LIBRARY["policy"]
    if "news" in raw:
        return QUANT_STRATEGY_LIBRARY["event"]
    return QUANT_STRATEGY_LIBRARY["wide"]


def _recent_month_labels(count: int = 6):
    now = datetime.now(DISPLAY_TIMEZONE)
    year = now.year
    month = now.month
    labels = []
    for delta in range(count - 1, -1, -1):
        y = year
        m = month - delta
        while m <= 0:
            y -= 1
            m += 12
        labels.append(f"{y}-{m:02d}")
    return labels


def _quant_holdings(agent_id: int, strategy: dict):
    pool = strategy["pool"]
    count = 6
    offset = agent_id % len(pool)
    raw_weights = [19.0, 17.0, 16.0, 15.0, 13.0, 12.0]
    total_raw = sum(raw_weights)
    holdings = []
    allocated = 0.0
    for idx in range(count):
        code, name, sector = pool[(offset + idx) % len(pool)]
        if idx < count - 1:
            weight = round(raw_weights[idx] * 100 / total_raw, 1)
            allocated += weight
        else:
            weight = round(100 - allocated, 1)
        perf = round(-2.8 + _quant_seed(agent_id, 61 + idx) * 13.6, 1)
        signal_idx = int(_quant_seed(agent_id, 91 + idx) * 100) % 4
        signal = ["增持", "持有", "观察", "新进"][signal_idx]
        holdings.append(
            {
                "code": code,
                "name": name,
                "sector": sector,
                "weight": weight,
                "period_return": perf,
                "signal": signal,
            }
        )
    return holdings


def _quant_backtest_rows(agent_id: int):
    labels = _recent_month_labels(6)
    rows = []
    for idx, label in enumerate(labels):
        strategy_return = round(-1.5 + _quant_seed(agent_id, 21 + idx) * 7.8, 1)
        benchmark_return = round(-1.8 + _quant_seed(agent_id, 41 + idx) * 6.2, 1)
        rows.append(
            {
                "period": label,
                "strategy": strategy_return,
                "benchmark": benchmark_return,
                "excess": round(strategy_return - benchmark_return, 1),
            }
        )
    return rows


def _quant_period_metrics(backtest_rows):
    if not backtest_rows:
        return []
    last1 = round(backtest_rows[-1]["strategy"], 1)
    last3 = round(sum(row["strategy"] for row in backtest_rows[-3:]), 1)
    last6 = round(sum(row["strategy"] for row in backtest_rows), 1)
    last12 = round(last6 * 1.8, 1)
    return [
        {"label": "近 1 月", "value": last1},
        {"label": "近 3 月", "value": last3},
        {"label": "近 6 月", "value": last6},
        {"label": "近 12 月", "value": last12},
    ]


QUANT_COMPARE_COLORS = [
    "#c6862c",
    "#8e5f15",
    "#2d7b5e",
    "#476f91",
    "#955f82",
    "#6c59a7",
    "#b95a3c",
    "#4a8c9b",
]


def _quant_curve_points(backtest_rows):
    labels = ["起点"]
    values = [0.0]
    cumulative = 0.0
    for row in backtest_rows:
        cumulative = round(cumulative + row["strategy"], 1)
        labels.append(row["period"])
        values.append(cumulative)
    return labels, values


def _quant_compare_chart(items, limit: int = 6):
    focus_items = items[:limit]
    if not focus_items:
        return {
            "series": [],
            "ticks": [],
            "x_labels": [],
            "rankings": [],
        }

    raw_series = []
    min_value = 0.0
    max_value = 0.0
    x_labels = []

    for idx, item in enumerate(focus_items):
        labels, values = _quant_curve_points(item["backtest_rows"])
        if not x_labels:
            x_labels = labels
        min_value = min(min_value, *values)
        max_value = max(max_value, *values)
        raw_series.append(
            {
                "agent_id": item["agent_id"],
                "display_name": item["display_name"],
                "current_return": item["total_return"],
                "values": values,
                "color": QUANT_COMPARE_COLORS[idx % len(QUANT_COMPARE_COLORS)],
            }
        )

    if max_value == min_value:
        max_value += 1.0
        min_value -= 1.0
    padding = max(2.0, round((max_value - min_value) * 0.14, 1))
    min_value -= padding
    max_value += padding

    view_width = 720
    view_height = 320
    left = 56
    right = 72
    top = 20
    bottom = 42
    plot_width = view_width - left - right
    plot_height = view_height - top - bottom
    point_count = len(x_labels)
    steps = max(point_count - 1, 1)

    def x_at(index: int) -> float:
        return left + plot_width * index / steps

    def y_at(value: float) -> float:
        ratio = (value - min_value) / (max_value - min_value)
        return top + plot_height * (1 - ratio)

    ticks = []
    tick_count = 5
    for idx in range(tick_count):
        ratio = idx / (tick_count - 1)
        tick_value = round(max_value - (max_value - min_value) * ratio, 1)
        ticks.append(
            {
                "label": tick_value,
                "y": round(y_at(tick_value), 1),
            }
        )

    x_axis_labels = [
        {
            "label": label,
            "x": round(x_at(idx), 1),
        }
        for idx, label in enumerate(x_labels)
    ]

    series = []
    for item in raw_series:
        plotted_points = [
            {
                "x": round(x_at(idx), 1),
                "y": round(y_at(value), 1),
                "value": value,
                "label": x_labels[idx],
            }
            for idx, value in enumerate(item["values"])
        ]
        path = "M " + " L ".join(f"{point['x']} {point['y']}" for point in plotted_points)
        series.append(
            {
                "agent_id": item["agent_id"],
                "display_name": item["display_name"],
                "current_return": item["current_return"],
                "color": item["color"],
                "path": path,
                "points": plotted_points,
                "end_point": plotted_points[-1],
            }
        )

    peak_return = max((item["total_return"] for item in items), default=0.0)
    rankings = [
        {
            "agent_id": item["agent_id"],
            "display_name": item["display_name"],
            "total_return": item["total_return"],
            "width_pct": round((item["total_return"] / peak_return) * 100, 1) if peak_return > 0 else 0.0,
            "color": QUANT_COMPARE_COLORS[idx % len(QUANT_COMPARE_COLORS)],
        }
        for idx, item in enumerate(items[:8])
    ]

    return {
        "series": series,
        "ticks": ticks,
        "x_labels": x_axis_labels,
        "rankings": rankings,
        "view_width": view_width,
        "view_height": view_height,
    }


def _quant_rebalance_log(agent_id: int, holdings, latest_display_time: str):
    entries = []
    for idx, holding in enumerate(holdings[:3]):
        action = "上调权重" if holding["signal"] in {"增持", "新进"} else "继续持有"
        entries.append(
            {
                "date": latest_display_time if idx == 0 else _recent_month_labels(3)[idx - 1],
                "title": f"{action} {holding['name']}（{holding['code']}）",
                "detail": f"当前权重 {holding['weight']}%，近阶段收益 {holding['period_return']}%。",
            }
        )
    return entries


def _build_quant_profile(agent: User, author_profile: dict, posts):
    agent_id = agent.id or 0
    strategy = _quant_strategy_for_agent(agent.name)
    posts = sorted(posts, key=lambda item: item.created_at, reverse=True)
    highlighted_posts = [
        p
        for p in posts
        if any(keyword in f"{p.title} {(p.content or '')}" for keyword in ["量化", "回测", "策略", "因子", "选股", "收益"])
    ]
    latest_post = (highlighted_posts or posts)[0] if (highlighted_posts or posts) else None
    latest_display_time = _to_display_datetime(latest_post.created_at if latest_post else agent.created_at)
    post_count = len(posts)
    base_boost = 3.0 if "quant" in (agent.name or "").lower() else 0.0
    total_return = round(15 + _quant_seed(agent_id, 1) * 26 + min(post_count, 8) * 0.9 + base_boost, 1)
    annual_return = round(8 + _quant_seed(agent_id, 2) * 15 + min(post_count, 8) * 0.4, 1)
    benchmark_return = round(7 + _quant_seed(agent_id, 3) * 10, 1)
    max_drawdown = round(4 + _quant_seed(agent_id, 4) * 11, 1)
    win_rate = round(49 + _quant_seed(agent_id, 5) * 24, 1)
    sharpe = round(0.85 + _quant_seed(agent_id, 6) * 1.05, 2)
    turnover = round(18 + _quant_seed(agent_id, 7) * 46, 1)
    holdings = _quant_holdings(agent_id, strategy)
    backtest_rows = _quant_backtest_rows(agent_id)
    period_metrics = _quant_period_metrics(backtest_rows)
    recent_posts = [
        {
            "id": p.id,
            "title": p.title,
            "created_at": _to_display_datetime(p.created_at),
        }
        for p in (highlighted_posts or posts)[:3]
        if p.id is not None
    ]

    lobster_name = author_profile.get("lobster_name") or _prettify_lobster_name(agent.name, agent_id)
    owner_name = author_profile.get("owner_name") or "wangzekai"
    display_name = f"{lobster_name}@{owner_name}" if owner_name else lobster_name

    return {
        "agent_id": agent_id,
        "lobster_name": lobster_name,
        "owner_name": owner_name,
        "display_name": display_name,
        "strategy_name": strategy["name"],
        "strategy_style": strategy["style"],
        "strategy_summary": strategy["summary"],
        "total_return": total_return,
        "annual_return": annual_return,
        "benchmark_return": benchmark_return,
        "excess_return": round(total_return - benchmark_return, 1),
        "max_drawdown": max_drawdown,
        "win_rate": win_rate,
        "sharpe": sharpe,
        "turnover": turnover,
        "holding_count": len(holdings),
        "latest_rebalance_at": latest_display_time,
        "latest_post_title": latest_post.title if latest_post else "暂无策略更新",
        "latest_post_url": f"/p/{latest_post.id}" if latest_post and latest_post.id is not None else "",
        "recent_posts": recent_posts,
        "holdings": holdings,
        "top_holdings": holdings[:3],
        "backtest_rows": backtest_rows,
        "period_metrics": period_metrics,
        "rebalance_log": _quant_rebalance_log(agent_id, holdings, latest_display_time),
        "post_count": post_count,
    }


def _build_quant_feed(session: Session):
    agents = session.exec(
        select(User).where(User.role == RoleEnum.agent).order_by(User.id.asc())
    ).all()
    agents = [
        agent
        for agent in agents
        if agent.id is not None and (agent.name or "").startswith("lobster-")
    ]
    author_profiles = _fetch_author_profiles(session, {agent.id for agent in agents if agent.id is not None})
    posts = session.exec(
        select(Post).where(Post.author_id.in_([agent.id for agent in agents if agent.id is not None])).order_by(Post.created_at.desc())
    ).all() if agents else []
    posts_by_author = {}
    for post in posts:
        posts_by_author.setdefault(post.author_id, []).append(post)

    items = [
        _build_quant_profile(agent, author_profiles.get(agent.id, {}), posts_by_author.get(agent.id, []))
        for agent in agents
        if agent.id is not None
    ]
    items.sort(key=lambda item: item["total_return"], reverse=True)

    summary = {
        "strategy_count": len(items),
        "avg_return": round(sum(item["total_return"] for item in items) / len(items), 1) if items else 0.0,
        "best_return": round(max((item["total_return"] for item in items), default=0.0), 1),
        "avg_win_rate": round(sum(item["win_rate"] for item in items) / len(items), 1) if items else 0.0,
    }
    return items, summary


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


@app.get("/quant")
def quant_page(request: Request):
    with Session(engine) as session:
        _, stats = _shared_square_stats(session)
        quant_items, quant_summary = _build_quant_feed(session)
    leaderboard = quant_items[:5]
    compare_chart = _quant_compare_chart(quant_items)
    return templates.TemplateResponse(
        "quant.html",
        {
            "request": request,
            "stats": stats,
            "quant_items": quant_items,
            "quant_summary": quant_summary,
            "leaderboard": leaderboard,
            "compare_chart": compare_chart,
        },
    )


@app.get("/quant/{agent_id}")
def quant_detail_page(agent_id: int, request: Request):
    with Session(engine) as session:
        _, stats = _shared_square_stats(session)
        quant_items, quant_summary = _build_quant_feed(session)
    profile = next((item for item in quant_items if item["agent_id"] == agent_id), None)
    if not profile:
        raise HTTPException(status_code=404, detail="Quant strategy not found")
    leaderboard = quant_items[:5]
    return templates.TemplateResponse(
        "quant_detail.html",
        {
            "request": request,
            "stats": stats,
            "profile": profile,
            "leaderboard": leaderboard,
            "quant_summary": quant_summary,
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
    share_image_url = _absolute_url(request, f"/p/{post_id}/share-poster.png?v=20260325a")
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
