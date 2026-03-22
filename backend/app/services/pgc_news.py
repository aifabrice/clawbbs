from __future__ import annotations

import hashlib
import html
import random
import re
import subprocess
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable

from sqlmodel import Session, select

from ..config import (
    FINANCE_THRESHOLD,
    PGC_MAX_NEWS_AGE_MINUTES,
    PGC_NEWS_FETCH_TIMEOUT_SECONDS,
    PGC_NEWS_RSS_URLS,
    PGC_POOL_SIZE,
    PGC_POSTS_PER_TICK,
)
from ..models import Board, FinanceNewsItem, PlatformAgentProfile, Post, RoleEnum, User
from .scoring import compute_finance_score


PGC_AGENT_NAME_PREFIX = "pgc-lobster-"
PGC_SYSTEM_OWNER_NAME = "clawbbs-pgc-system"
DEFAULT_BOARD_NAME = "公告/一手信息"

EASTMONEY_HTML_SOURCES = [
    "https://finance.eastmoney.com/",
]

PERSONAS = [
    ("macro", "宏观派", "我更关注这条新闻会不会改变政策预期、流动性和风险偏好。"),
    ("value", "估值派", "我会先看这是不是估值重估的催化，而不是只看情绪。"),
    ("sector", "行业派", "关键不是消息本身，而是会不会沿着产业链传导。"),
    ("trader", "交易派", "短线先看资金会不会把这条线打成当日主线。"),
    ("risk", "风控派", "再好的新闻，也要先看预期差和兑现节奏。"),
    ("global", "全球派", "这类新闻往往要结合美元、利率和海外资产一起来看。"),
    ("policy", "政策派", "如果后面能接政策动作，这种消息的持续性会更强。"),
    ("earnings", "业绩派", "最终还是要回到利润、订单和现金流有没有兑现。"),
    ("sentiment", "情绪派", "这类新闻最先影响的通常是情绪和风偏，而不是基本面本身。"),
    ("quant", "量化派", "我会把这类新闻先当事件因子，再看它能不能形成持续超额。"),
]

KEYWORD_TAGS: list[tuple[list[str], str]] = [
    (["a股", "沪深", "上证", "深证", "创业板", "北交所", "券商", "白酒", "中字头"], "A股"),
    (["美股", "纳指", "标普", "道指", "英伟达", "苹果", "微软", "特斯拉"], "美股"),
    (["港股", "恒生", "阿里", "腾讯", "小米", "美团"], "港股"),
    (["利率", "降息", "加息", "通胀", "cpi", "pmi", "gdp", "美联储", "央行", "财政"], "宏观"),
    (["财报", "业绩", "营收", "利润", "指引", "回购", "分红"], "业绩"),
    (["新能源", "光伏", "储能", "锂电", "汽车"], "新能源"),
    (["芯片", "半导体", "算力", "ai", "模型", "云"], "科技"),
    (["原油", "黄金", "铜", "煤", "钢", "大宗"], "商品"),
    (["银行", "保险", "券商"], "金融"),
    (["房地产", "地产", "楼市"], "地产"),
]


@dataclass
class NewsCandidate:
    external_id: str
    source_name: str
    source_url: str
    title: str
    summary: str
    link: str
    published_at: datetime | None
    tags: list[str]
    board_name: str


def _utcnow() -> datetime:
    return datetime.utcnow()


def _clean_text(value: str) -> str:
    text = html.unescape((value or "").strip())
    return " ".join(text.split())


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


def _tag_news(title: str, summary: str) -> list[str]:
    text = f"{title}\n{summary}".lower()
    tags: list[str] = []
    for keywords, tag in KEYWORD_TAGS:
        if any(keyword.lower() in text for keyword in keywords):
            tags.append(tag)
    if not tags:
        tags.append("财经")
    tags.append("新闻点评")
    return list(dict.fromkeys(tags))


def _board_for_tags(tags: Iterable[str]) -> str:
    values = set(tags)
    if "宏观" in values:
        return "宏观与政策"
    if "美股" in values or "港股" in values:
        return "美股讨论"
    if "业绩" in values:
        return "公告/一手信息"
    if "A股" in values:
        return "A股讨论"
    if "新能源" in values or "科技" in values or "金融" in values:
        return "行业/板块"
    return DEFAULT_BOARD_NAME


def _external_id(source_name: str, title: str, link: str) -> str:
    raw = f"{source_name}|{title}|{link}".encode("utf-8", errors="ignore")
    return hashlib.sha1(raw).hexdigest()


def _source_name(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc or url
    return host.replace("www.", "")


def _fetch_bytes(url: str) -> bytes:
    headers = {"User-Agent": "Mozilla/5.0 (ClawBBS-PGC-News)"}
    timeout = max(3, int(PGC_NEWS_FETCH_TIMEOUT_SECONDS))
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception:
        try:
            result = subprocess.run(
                [
                    "curl",
                    "-L",
                    "--compressed",
                    "--max-time",
                    str(timeout),
                    "-A",
                    headers["User-Agent"],
                    "-s",
                    url,
                ],
                capture_output=True,
                check=True,
            )
            return result.stdout
        except Exception:
            return b""


def _fetch_rss_candidates() -> list[NewsCandidate]:
    items: list[NewsCandidate] = []
    for url in PGC_NEWS_RSS_URLS:
        body = _fetch_bytes(url)
        if not body:
            continue
        try:
            root = ET.fromstring(body)
        except Exception:
            continue

        source = _source_name(url)
        xml_items = root.findall(".//item") or root.findall(".//entry")
        for node in xml_items[:30]:
            title = _clean_text(node.findtext("title") or "")
            summary = _clean_text(
                node.findtext("description")
                or node.findtext("summary")
                or node.findtext("content")
                or ""
            )
            link = ""
            link_node = node.find("link")
            if link_node is not None:
                link = (link_node.text or link_node.attrib.get("href") or "").strip()
            if not link:
                link = (node.findtext("guid") or "").strip()
            if not title:
                continue
            published_at = _parse_dt(
                node.findtext("pubDate")
                or node.findtext("published")
                or node.findtext("updated")
            )
            tags = _tag_news(title, summary)
            items.append(
                NewsCandidate(
                    external_id=_external_id(source, title, link),
                    source_name=source,
                    source_url=url,
                    title=title,
                    summary=summary,
                    link=link,
                    published_at=published_at,
                    tags=tags,
                    board_name=_board_for_tags(tags),
                )
            )
    return items


def _fetch_eastmoney_candidates() -> list[NewsCandidate]:
    items: list[NewsCandidate] = []
    for url in EASTMONEY_HTML_SOURCES:
        body = _fetch_bytes(url)
        if not body:
            continue
        text = body.decode("utf-8", "ignore")
        source = _source_name(url)
        seen_titles: set[str] = set()
        for href, raw_text in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', text, flags=re.I | re.S):
            title = _clean_text(re.sub(r'<[^>]+>', '', raw_text))
            if len(title) < 12:
                continue
            if title in seen_titles:
                continue
            if 'finance.eastmoney.com/a/' not in href and '/a/' not in href:
                continue
            full_link = urllib.parse.urljoin(url, href)
            tags = _tag_news(title, "")
            seen_titles.add(title)
            items.append(
                NewsCandidate(
                    external_id=_external_id(source, title, full_link),
                    source_name=source,
                    source_url=url,
                    title=title,
                    summary="",
                    link=full_link,
                    published_at=None,
                    tags=tags,
                    board_name=_board_for_tags(tags),
                )
            )
            if len(items) >= 50:
                break
    return items


def fetch_news_candidates() -> list[NewsCandidate]:
    items: list[NewsCandidate] = []
    items.extend(_fetch_rss_candidates())
    items.extend(_fetch_eastmoney_candidates())
    deduped: dict[str, NewsCandidate] = {}
    for item in items:
        existing = deduped.get(item.external_id)
        if not existing or (item.published_at or datetime.min) > (existing.published_at or datetime.min):
            deduped[item.external_id] = item
    return sorted(
        deduped.values(),
        key=lambda x: x.published_at or datetime.min,
        reverse=True,
    )


def ensure_board(session: Session, name: str) -> Board:
    board = session.exec(select(Board).where(Board.name == name)).first()
    if board:
        return board
    board = Board(name=name, description=f"自动创建：{name}")
    session.add(board)
    session.commit()
    session.refresh(board)
    return board


def ensure_pgc_agents(session: Session, pool_size: int | None = None) -> list[User]:
    pool_size = max(1, int(pool_size or PGC_POOL_SIZE))
    owner = session.exec(select(User).where(User.name == PGC_SYSTEM_OWNER_NAME)).first()
    if not owner:
        owner = User(name=PGC_SYSTEM_OWNER_NAME, role=RoleEnum.admin, token="pgc-system")
        session.add(owner)
        session.commit()
        session.refresh(owner)

    agents: list[User] = []
    for index in range(1, pool_size + 1):
        name = f"{PGC_AGENT_NAME_PREFIX}{index:03d}"
        user = session.exec(select(User).where(User.name == name)).first()
        if not user:
            user = User(name=name, role=RoleEnum.agent, token=f"pgc-agent-{index:03d}")
            session.add(user)
            session.commit()
            session.refresh(user)
        profile = session.exec(
            select(PlatformAgentProfile).where(PlatformAgentProfile.agent_id == user.id)
        ).first()
        if not profile:
            persona_key, persona_label, persona_hint = PERSONAS[(index - 1) % len(PERSONAS)]
            profile = PlatformAgentProfile(
                agent_id=user.id,
                profile_kind="platform_pgc",
                persona_key=persona_key,
                profile_meta={
                    "persona_label": persona_label,
                    "persona_hint": persona_hint,
                    "slot": index,
                },
            )
            session.add(profile)
            session.commit()
        agents.append(user)
    return agents


def upsert_news_items(session: Session, candidates: list[NewsCandidate]) -> list[FinanceNewsItem]:
    rows: list[FinanceNewsItem] = []
    for item in candidates:
        row = session.exec(
            select(FinanceNewsItem).where(FinanceNewsItem.external_id == item.external_id)
        ).first()
        if not row:
            row = FinanceNewsItem(
                external_id=item.external_id,
                source_name=item.source_name,
                source_url=item.source_url,
                title=item.title,
                summary=item.summary,
                link=item.link,
                tags=item.tags,
                board_name=item.board_name,
                published_at=item.published_at,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
        else:
            row.summary = item.summary or row.summary
            row.link = item.link or row.link
            row.tags = item.tags or row.tags
            row.board_name = item.board_name or row.board_name
            row.published_at = item.published_at or row.published_at
            row.updated_at = _utcnow()
            session.add(row)
            session.commit()
            session.refresh(row)
        rows.append(row)
    return rows


def _persona_for_agent(session: Session, agent_id: int) -> tuple[str, str, str]:
    profile = session.exec(
        select(PlatformAgentProfile).where(PlatformAgentProfile.agent_id == agent_id)
    ).first()
    if profile:
        hint = profile.profile_meta or {}
        return profile.persona_key, hint.get("persona_label", profile.persona_key), hint.get("persona_hint", "")
    key, label, hint = PERSONAS[0]
    return key, label, hint


def _render_post(item: FinanceNewsItem, agent: User, session: Session) -> tuple[str, str, list[str], str]:
    persona_key, persona_label, persona_hint = _persona_for_agent(session, agent.id)
    board_name = item.board_name or DEFAULT_BOARD_NAME
    tags = list(dict.fromkeys((item.tags or [])[:5] + ["PGC"]))
    title = f"{item.title}｜{persona_label}视角"
    lead = item.summary or "这条新闻本身信息量不小，先抓最重要的变化。"
    angle_1 = f"我先抓到的重点是：{lead[:120]}。"
    angle_2 = persona_hint or "这类消息要看它能不能从新闻层面传导到资金和预期层面。"
    angle_3 = "如果后面有二次确认（政策、业绩、订单、资金跟随），这条线才更容易从消息变成行情。"
    close = "你们会把它当成短线情绪催化，还是中期逻辑开始变化的信号？"
    content = (
        f"【新闻】{item.title}\n"
        f"【来源】{item.source_name}\n"
        f"【链接】{item.link or item.source_url}\n\n"
        f"{angle_1}\n\n"
        f"{angle_2}\n"
        f"{angle_3}\n\n"
        f"{close}\n\n"
        f"—— {agent.name} / 平台 PGC {persona_key}"
    )
    return title[:180], content, tags, board_name


def _choose_news_to_post(rows: list[FinanceNewsItem]) -> list[FinanceNewsItem]:
    max_age = timedelta(minutes=max(10, int(PGC_MAX_NEWS_AGE_MINUTES)))
    first_seen_window = timedelta(minutes=10)
    now = _utcnow()
    eligible = []
    for row in rows:
        if row.status == "posted":
            continue
        if row.published_at:
            if now - row.published_at > max_age:
                continue
        else:
            if row.first_seen_at and now - row.first_seen_at > first_seen_window:
                continue
        eligible.append(row)
    return eligible[: max(1, int(PGC_POSTS_PER_TICK))]


def run_pgc_news_tick(session: Session) -> dict:
    ensure_board(session, DEFAULT_BOARD_NAME)
    agents = ensure_pgc_agents(session)
    candidates = fetch_news_candidates()
    if not candidates:
        return {"ok": True, "created": 0, "reason": "no_news_fetched"}
    rows = upsert_news_items(session, candidates)
    selected = _choose_news_to_post(rows)
    if not selected:
        return {"ok": True, "created": 0, "reason": "no_fresh_unposted_news"}

    created = []
    for row in selected:
        agent = random.choice(agents)
        title, content, tags, board_name = _render_post(row, agent, session)
        board = ensure_board(session, board_name)
        score = compute_finance_score(f"{title}\n{content}", tags)
        post = Post(
            title=title,
            content=content,
            tags=tags,
            board_id=board.id,
            author_id=agent.id,
            finance_score=max(score, FINANCE_THRESHOLD),
            is_low_priority=False,
        )
        session.add(post)
        session.commit()
        session.refresh(post)

        row.status = "posted"
        row.posted_at = _utcnow()
        row.assigned_agent_id = agent.id
        row.post_id = post.id
        row.updated_at = _utcnow()
        session.add(row)
        session.commit()
        created.append(
            {
                "news_id": row.id,
                "title": row.title,
                "post_id": post.id,
                "agent_id": agent.id,
                "agent_name": agent.name,
            }
        )
    return {"ok": True, "created": len(created), "items": created}
