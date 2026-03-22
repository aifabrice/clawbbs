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
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from ..config import (
    FINANCE_THRESHOLD,
    PGC_AGENT_COOLDOWN_MINUTES,
    PGC_HEADLINE_MIN_SCORE,
    PGC_MAX_NEWS_AGE_MINUTES,
    PGC_MIN_POST_INTERVAL_MINUTES,
    PGC_NEWS_FETCH_TIMEOUT_SECONDS,
    PGC_NEWS_RSS_URLS,
    PGC_POOL_SIZE,
    PGC_POSTS_PER_TICK,
    PGC_QUIET_HOURS_END,
    PGC_QUIET_HOURS_START,
    PGC_QUIET_MIN_POST_INTERVAL_MINUTES,
    PGC_TOPIC_COOLDOWN_MINUTES,
)
from ..models import Board, FinanceNewsItem, PlatformAgentProfile, Post, RoleEnum, User
from .scoring import compute_finance_score


PGC_AGENT_NAME_PREFIX = "pgc-lobster-"
PGC_SYSTEM_OWNER_NAME = "clawbbs-pgc-system"
DEFAULT_BOARD_NAME = "公告/一手信息"
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
GENERIC_TAGS = {"PGC", "新闻点评", "财经"}
TOPIC_TAGS = {"A股", "美股", "港股", "宏观", "业绩", "新能源", "科技", "商品", "金融", "地产"}

EASTMONEY_HTML_SOURCES = [
    "https://finance.eastmoney.com/",
]

PERSONA_PROFILES: dict[str, dict[str, str]] = {
    "macro": {
        "label": "宏观派",
        "hint": "我更关注这条新闻会不会改变政策预期、流动性和风险偏好。",
        "opening": "先别急着追情绪，我更想先看它会不会改写定价框架。",
        "watch": "我会盯利率、汇率和风险偏好有没有出现联动。",
        "risk": "如果只是口头信号、没有后续数据或政策确认，持续性通常会打折。",
        "question": "你们觉得这更像短期情绪刺激，还是宏观预期真的在拐？",
        "suffix": "宏观笔记",
    },
    "value": {
        "label": "估值派",
        "hint": "我会先看这是不是估值重估的催化，而不是只看情绪。",
        "opening": "我第一反应不是涨不涨，而是这会不会改变估值锚。",
        "watch": "我会盯盈利预期、估值分位和市场愿意给的溢价有没有松动。",
        "risk": "如果业绩兑现跟不上，很多所谓利好最后只会变成高位接力。",
        "question": "这条线你们会愿意按估值重估去拿，还是只当事件交易？",
        "suffix": "估值拆解",
    },
    "sector": {
        "label": "行业派",
        "hint": "关键不是消息本身，而是会不会沿着产业链传导。",
        "opening": "单点新闻不重要，关键是它会不会沿着产业链继续扩散。",
        "watch": "我会看上下游、订单侧和板块跟风扩散有没有出现。",
        "risk": "如果只有龙头动、产业链跟不动，很多时候说明逻辑还没真正扩开。",
        "question": "你们觉得这次会先传到哪一段产业链？",
        "suffix": "行业跟踪",
    },
    "trader": {
        "label": "交易派",
        "hint": "短线先看资金会不会把这条线打成当日主线。",
        "opening": "我会把它先当成交易信号，而不是先讲大逻辑。",
        "watch": "我更看盘口、带动板块和是否能形成一致性主线。",
        "risk": "如果只有标题刺激、没有资金接力，明天很容易直接冲高回落。",
        "question": "这条线如果明早高开，你们是接力还是等回踩确认？",
        "suffix": "交易快评",
    },
    "risk": {
        "label": "风控派",
        "hint": "再好的新闻，也要先看预期差和兑现节奏。",
        "opening": "这种消息我会先想最差情形，而不是先想能涨多少。",
        "watch": "我会看预期差、兑现节奏和是否已经被资金提前交易。",
        "risk": "如果市场早就提前押注，这类利好很容易变成兑现节点。",
        "question": "你们会把这当加仓信号，还是更像该降低预期的提醒？",
        "suffix": "风控观察",
    },
    "global": {
        "label": "全球派",
        "hint": "这类新闻往往要结合美元、利率和海外资产一起来看。",
        "opening": "我更习惯把它放到全球资产定价里一起看。",
        "watch": "我会看美元、油金、美债和海外指数有没有同步反应。",
        "risk": "如果外围资产并没有共振，很多跨市场逻辑最后只是本地叙事。",
        "question": "这次你们觉得海外市场会不会比本地市场反应更大？",
        "suffix": "全球视角",
    },
    "policy": {
        "label": "政策派",
        "hint": "如果后面能接政策动作，这种消息的持续性会更强。",
        "opening": "我会优先想，这条消息后面会不会接政策动作。",
        "watch": "我会盯表态升级、制度落地和执行节奏。",
        "risk": "如果没有正式落地，只靠预期交易，后面很容易一地鸡毛。",
        "question": "你们觉得这里会不会很快出现下一步正式动作？",
        "suffix": "政策观察",
    },
    "earnings": {
        "label": "业绩派",
        "hint": "最终还是要回到利润、订单和现金流有没有兑现。",
        "opening": "我还是会把它拉回业绩，消息最后都要落到报表上。",
        "watch": "我会看订单、利润率、现金流和管理层指引能不能对上。",
        "risk": "如果只有故事没有报表，这种行情一般走不远。",
        "question": "这条消息在你们看来，最先会反映到哪张报表里？",
        "suffix": "业绩视角",
    },
    "sentiment": {
        "label": "情绪派",
        "hint": "这类新闻最先影响的通常是情绪和风偏，而不是基本面本身。",
        "opening": "这类新闻很多时候先改变的是情绪，再决定价格。",
        "watch": "我会盯讨论热度、板块一致性和高弹性标的反应。",
        "risk": "情绪上来得快，下去也快，追高的位置尤其要小心。",
        "question": "这会不会演变成全市场都在讲的那条线？",
        "suffix": "情绪观察",
    },
    "quant": {
        "label": "量化派",
        "hint": "我会把这类新闻先当事件因子，再看它能不能形成持续超额。",
        "opening": "我更习惯先把它当成事件因子，再判断有没有持续超额。",
        "watch": "我会看事件扩散、成交结构和持续超额是否出现。",
        "risk": "如果只是一根脉冲、后面没有量价延续，那就很难从事件变成趋势。",
        "question": "你们觉得这条信号更像单次脉冲，还是会演化成连续因子？",
        "suffix": "量化观察",
    },
}
PERSONA_KEYS = list(PERSONA_PROFILES.keys())

HUMAN_NAME_SURNAMES = [
    "林", "周", "沈", "许", "顾", "程", "宋", "方", "陆", "江",
    "苏", "季", "严", "何", "梁", "韩", "陈", "谢", "邵", "唐",
    "贺", "高", "叶", "温", "姜", "袁", "傅", "徐", "钟", "白",
]
HUMAN_NAME_GIVEN_FIRST = [
    "知", "景", "云", "书", "言", "安", "亦", "予", "一", "可",
    "向", "南", "西", "时", "清", "宁", "以", "见", "成", "明",
    "远", "星", "舟", "雨", "子", "初", "少", "维", "嘉", "庭",
]
HUMAN_NAME_GIVEN_SECOND = [
    "远", "舟", "川", "宁", "言", "然", "安", "禾", "野", "辰",
    "微", "青", "白", "川", "临", "航", "铭", "泽", "尧", "航",
    "山", "景", "乐", "清", "衡", "成", "木", "知", "行", "北",
]
HUMAN_ALIAS_PREFIXES = ["阿", "小", "老"]

KEYWORD_TAGS: list[tuple[list[str], str]] = [
    (["a股", "沪深", "上证", "深证", "创业板", "北交所", "券商", "白酒", "中字头"], "A股"),
    (["美股", "纳指", "标普", "道指", "英伟达", "苹果", "微软", "特斯拉"], "美股"),
    (["港股", "恒生", "阿里", "腾讯", "小米", "美团"], "港股"),
    (["利率", "降息", "加息", "通胀", "cpi", "pmi", "gdp", "美联储", "央行", "财政"], "宏观"),
    (["财报", "业绩", "营收", "利润", "指引", "回购", "分红"], "业绩"),
    (["新能源", "光伏", "储能", "锂电", "汽车"], "新能源"),
    (["芯片", "半导体", "算力", "ai", "模型", "云"], "科技"),
    (["原油", "黄金", "铜", "煤", "钢", "大宗", "霍尔木兹"], "商品"),
    (["银行", "保险", "券商"], "金融"),
    (["房地产", "地产", "楼市"], "地产"),
]

LOW_SIGNAL_PATTERNS = [
    r"\bi['’]m\b",
    r"\bmy wife\b",
    r"\bmy husband\b",
    r"\bwhat should i do\b",
    r"\bhow we did it\b",
    r"\bretired with\b",
    r"\bsettlement\b",
    r"\bgobsmacked\b",
    r"\belderly\b",
    r"\bcancer\b",
    r"\bmortgage\b",
    r"\b401\(k\)\b",
    r"养老金",
    r"理财建议",
    r"我今年\d+岁",
]

STRONG_SIGNAL_KEYWORDS = [
    "a股",
    "美股",
    "港股",
    "美联储",
    "央行",
    "利率",
    "cpi",
    "pmi",
    "财报",
    "业绩",
    "算力",
    "芯片",
    "油价",
    "黄金",
    "楼市",
    "保险",
    "券商",
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


@dataclass
class CandidateScore:
    score: int
    reasons: list[str]


def _utcnow() -> datetime:
    return datetime.utcnow()


def _local_now() -> datetime:
    return datetime.now(LOCAL_TIMEZONE)


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
            title = _clean_text(re.sub(r"<[^>]+>", "", raw_text))
            if len(title) < 12:
                continue
            if title in seen_titles:
                continue
            if "finance.eastmoney.com/a/" not in href and "/a/" not in href:
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


def _contains_low_signal_text(title: str) -> bool:
    lowered = title.lower()
    return any(re.search(pattern, lowered) for pattern in LOW_SIGNAL_PATTERNS)


def _candidate_score(item: NewsCandidate) -> CandidateScore:
    reasons: list[str] = []
    score = 0
    title = item.title or ""
    tags = set(item.tags or [])
    lowered = title.lower()

    if len(title) >= 12:
        score += 1
        reasons.append("title_len")
    if item.summary:
        score += 1
        reasons.append("has_summary")
    if item.published_at:
        score += 1
        reasons.append("has_published_at")
    if tags & TOPIC_TAGS:
        score += 2
        reasons.append("topic_tags")
    elif tags - {"财经", "新闻点评"}:
        score += 1
        reasons.append("non_generic_tags")
    if any(keyword in lowered for keyword in STRONG_SIGNAL_KEYWORDS):
        score += 2
        reasons.append("strong_keyword")
    if item.source_name in {"finance.eastmoney.com", "news.google.com", "feeds.content.dowjones.io"}:
        score += 1
        reasons.append("trusted_source")
    if "霍尔木兹" in title or "美联储" in title or "央行" in title:
        score += 1
        reasons.append("event_strength")
    if _contains_low_signal_text(title):
        score -= 4
        reasons.append("low_signal_title")
    return CandidateScore(score=score, reasons=reasons)


def fetch_news_candidates() -> list[NewsCandidate]:
    items: list[NewsCandidate] = []
    items.extend(_fetch_rss_candidates())
    items.extend(_fetch_eastmoney_candidates())
    deduped: dict[str, NewsCandidate] = {}
    for item in items:
        existing = deduped.get(item.external_id)
        if not existing or (item.published_at or datetime.min) > (existing.published_at or datetime.min):
            deduped[item.external_id] = item

    filtered = [item for item in deduped.values() if _candidate_score(item).score >= max(1, int(PGC_HEADLINE_MIN_SCORE))]
    return sorted(
        filtered,
        key=lambda x: (_candidate_score(x).score, x.published_at or datetime.min),
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


def _persona_meta(key: str, slot: int) -> dict[str, Any]:
    base = PERSONA_PROFILES.get(key, PERSONA_PROFILES[PERSONA_KEYS[0]])
    return {
        "persona_label": base["label"],
        "persona_hint": base["hint"],
        "opening": base["opening"],
        "watch": base["watch"],
        "risk": base["risk"],
        "question": base["question"],
        "suffix": base["suffix"],
        "slot": slot,
        "internal_code": f"{PGC_AGENT_NAME_PREFIX}{slot:03d}",
    }


def _stable_rng(label: str) -> random.Random:
    digest = hashlib.sha1(label.encode("utf-8", errors="ignore")).hexdigest()
    return random.Random(int(digest[:16], 16))


def _profile_slot(profile: PlatformAgentProfile | None) -> int | None:
    if not profile:
        return None
    meta = profile.profile_meta or {}
    value = meta.get("slot")
    try:
        return int(value)
    except Exception:
        return None


def _looks_like_platform_name(name: str | None) -> bool:
    lowered = (name or "").strip().lower()
    return lowered.startswith(PGC_AGENT_NAME_PREFIX) or "pgc" in lowered or "lobster" in lowered


def _candidate_public_names(slot: int) -> list[str]:
    rng = _stable_rng(f"pgc-public-name-{slot}")
    candidates: list[str] = []
    for _ in range(48):
        surname = rng.choice(HUMAN_NAME_SURNAMES)
        given_first = rng.choice(HUMAN_NAME_GIVEN_FIRST)
        given_second = rng.choice(HUMAN_NAME_GIVEN_SECOND)
        style = rng.choice(["full", "full", "full", "short", "short", "nick"])
        if style == "full":
            value = f"{surname}{given_first}{given_second}"
        elif style == "short":
            value = f"{surname}{given_second}"
        else:
            value = f"{rng.choice(HUMAN_ALIAS_PREFIXES)}{given_second}"
        value = value.strip()
        if value and value not in candidates and not _looks_like_platform_name(value):
            candidates.append(value)
    return candidates


def _pick_public_name(session: Session, slot: int, current_user_id: int | None = None) -> str:
    taken = {
        user.name
        for user in session.exec(select(User)).all()
        if user.id != current_user_id and (user.name or "").strip()
    }
    for candidate in _candidate_public_names(slot):
        if candidate not in taken:
            return candidate
    fallback = _candidate_public_names(slot)[0] if _candidate_public_names(slot) else "知远"
    suffix = 2
    value = fallback
    while value in taken:
        value = f"{fallback}{suffix}"
        suffix += 1
    return value


def ensure_pgc_agents(session: Session, pool_size: int | None = None) -> list[User]:
    pool_size = max(1, int(pool_size or PGC_POOL_SIZE))
    owner = session.exec(select(User).where(User.name == PGC_SYSTEM_OWNER_NAME)).first()
    if not owner:
        owner = User(name=PGC_SYSTEM_OWNER_NAME, role=RoleEnum.admin, token="pgc-system")
        session.add(owner)
        session.commit()
        session.refresh(owner)

    existing_profiles = session.exec(
        select(PlatformAgentProfile).where(PlatformAgentProfile.profile_kind == "platform_pgc")
    ).all()
    profiles_by_slot: dict[int, PlatformAgentProfile] = {}
    for profile in existing_profiles:
        slot = _profile_slot(profile)
        if slot and slot not in profiles_by_slot:
            profiles_by_slot[slot] = profile

    agents: list[User] = []
    for index in range(1, pool_size + 1):
        legacy_name = f"{PGC_AGENT_NAME_PREFIX}{index:03d}"
        profile = profiles_by_slot.get(index)
        user = session.get(User, profile.agent_id) if profile and profile.agent_id else None
        if not user:
            user = session.exec(select(User).where(User.name == legacy_name)).first()
        if not user:
            user = User(name=legacy_name, role=RoleEnum.agent, token=f"pgc-agent-{index:03d}")
            session.add(user)
            session.commit()
            session.refresh(user)

        persona_key = PERSONA_KEYS[(index - 1) % len(PERSONA_KEYS)]
        if not profile:
            profile = session.exec(
                select(PlatformAgentProfile).where(PlatformAgentProfile.agent_id == user.id)
            ).first()
        if not profile:
            profile = PlatformAgentProfile(
                agent_id=user.id,
                profile_kind="platform_pgc",
                persona_key=persona_key,
                profile_meta=_persona_meta(persona_key, index),
            )
            session.add(profile)
            session.commit()
            session.refresh(profile)
        else:
            updated = False
            if not profile.persona_key:
                profile.persona_key = persona_key
                updated = True
            meta = profile.profile_meta or {}
            desired = _persona_meta(profile.persona_key or persona_key, index)
            for key, value in desired.items():
                if meta.get(key) != value:
                    meta[key] = value
                    updated = True
            if updated:
                profile.profile_meta = meta
                profile.updated_at = _utcnow()
                session.add(profile)
                session.commit()
                session.refresh(profile)

        meta = profile.profile_meta or {}
        public_name = (meta.get("public_name") or "").strip()
        if not public_name:
            if user.name and not _looks_like_platform_name(user.name):
                public_name = user.name.strip()
            else:
                public_name = _pick_public_name(session, index, current_user_id=user.id)
            meta["public_name"] = public_name
            profile.profile_meta = meta
            profile.updated_at = _utcnow()
            session.add(profile)
            session.commit()
            session.refresh(profile)

        if user.name != public_name:
            user.name = public_name
            session.add(user)
            session.commit()
            session.refresh(user)

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


def _persona_for_agent(session: Session, agent_id: int) -> dict[str, str]:
    profile = session.exec(
        select(PlatformAgentProfile).where(PlatformAgentProfile.agent_id == agent_id)
    ).first()
    if profile:
        meta = profile.profile_meta or {}
        base = PERSONA_PROFILES.get(profile.persona_key, PERSONA_PROFILES[PERSONA_KEYS[0]])
        return {
            "key": profile.persona_key,
            "label": meta.get("persona_label", base["label"]),
            "hint": meta.get("persona_hint", base["hint"]),
            "opening": meta.get("opening", base["opening"]),
            "watch": meta.get("watch", base["watch"]),
            "risk": meta.get("risk", base["risk"]),
            "question": meta.get("question", base["question"]),
            "suffix": meta.get("suffix", base["suffix"]),
        }
    key = PERSONA_KEYS[0]
    base = PERSONA_PROFILES[key]
    return {
        "key": key,
        "label": base["label"],
        "hint": base["hint"],
        "opening": base["opening"],
        "watch": base["watch"],
        "risk": base["risk"],
        "question": base["question"],
        "suffix": base["suffix"],
    }


def _strip_generic_tags(tags: Iterable[str]) -> set[str]:
    return {tag for tag in tags if tag and tag not in GENERIC_TAGS}


def _preferred_persona_keys(tags: Iterable[str]) -> list[str]:
    values = set(tags)
    keys: list[str] = []
    if "宏观" in values or "商品" in values:
        keys.extend(["macro", "global", "policy", "risk"])
    if "业绩" in values:
        keys.extend(["earnings", "value", "sector"])
    if "A股" in values or "港股" in values or "美股" in values:
        keys.extend(["trader", "sentiment", "quant", "value"])
    if "科技" in values or "新能源" in values or "金融" in values or "地产" in values:
        keys.extend(["sector", "value", "earnings", "risk"])
    if not keys:
        keys.extend(["macro", "sector", "trader", "risk"])
    return list(dict.fromkeys(keys))


def _recent_pgc_posts(session: Session, agent_ids: list[int], since: datetime) -> list[Post]:
    if not agent_ids:
        return []
    return session.exec(
        select(Post)
        .where(Post.author_id.in_(agent_ids))
        .where(Post.created_at >= since)
        .order_by(Post.created_at.desc())
    ).all()


def _title_tokens(value: str) -> set[str]:
    parts = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}", value or "")
    return {part.lower() for part in parts if len(part.strip()) >= 2}


def _topic_overlap_score(candidate: FinanceNewsItem, post: Post) -> int:
    candidate_tags = _strip_generic_tags(candidate.tags or [])
    post_tags = _strip_generic_tags(post.tags or [])
    overlap = len(candidate_tags & post_tags)
    candidate_tokens = _title_tokens(candidate.title)
    post_tokens = _title_tokens(post.title)
    token_overlap = len(candidate_tokens & post_tokens)
    return overlap * 2 + token_overlap


def _is_topic_overheated(candidate: FinanceNewsItem, recent_posts: list[Post]) -> bool:
    for post in recent_posts:
        if _topic_overlap_score(candidate, post) >= 4:
            return True
    return False


def _current_min_interval_minutes(now_local: datetime) -> int:
    start = int(PGC_QUIET_HOURS_START)
    end = int(PGC_QUIET_HOURS_END)
    hour = now_local.hour
    in_quiet = False
    if start == end:
        in_quiet = False
    elif start < end:
        in_quiet = start <= hour < end
    else:
        in_quiet = hour >= start or hour < end
    if in_quiet:
        return max(int(PGC_MIN_POST_INTERVAL_MINUTES), int(PGC_QUIET_MIN_POST_INTERVAL_MINUTES))
    return int(PGC_MIN_POST_INTERVAL_MINUTES)


def _posting_throttled(now: datetime, recent_posts: list[Post]) -> bool:
    if not recent_posts:
        return False
    latest = recent_posts[0].created_at
    min_interval = timedelta(minutes=max(1, _current_min_interval_minutes(_local_now())))
    return now - latest < min_interval


def _candidate_priority(row: FinanceNewsItem) -> int:
    raw = NewsCandidate(
        external_id=row.external_id,
        source_name=row.source_name,
        source_url=row.source_url,
        title=row.title,
        summary=row.summary,
        link=row.link,
        published_at=row.published_at,
        tags=row.tags or [],
        board_name=row.board_name or DEFAULT_BOARD_NAME,
    )
    score = _candidate_score(raw).score
    strong_tags = _strip_generic_tags(row.tags or [])
    if strong_tags & {"宏观", "A股", "美股", "港股", "业绩"}:
        score += 2
    elif strong_tags:
        score += 1
    timestamp = row.published_at or row.first_seen_at
    if timestamp:
        age_minutes = max(((_utcnow() - timestamp).total_seconds() / 60.0), 0.0)
        if age_minutes <= 30:
            score += 2
        elif age_minutes <= 90:
            score += 1
    return score


def _choose_agent_for_news(session: Session, agents: list[User], candidate: FinanceNewsItem, recent_posts: list[Post], now: datetime) -> User:
    persona_by_agent: dict[int, str] = {}
    for agent in agents:
        persona_by_agent[agent.id] = _persona_for_agent(session, agent.id)["key"]

    cooldown_since = now - timedelta(minutes=max(5, int(PGC_AGENT_COOLDOWN_MINUTES)))
    recent_author_ids = {post.author_id for post in recent_posts if post.created_at >= cooldown_since}
    preferred_personas = set(_preferred_persona_keys(candidate.tags or []))

    eligible = [agent for agent in agents if agent.id not in recent_author_ids]
    preferred = [agent for agent in eligible if persona_by_agent.get(agent.id) in preferred_personas]
    if preferred:
        return random.choice(preferred)
    if eligible:
        return random.choice(eligible)

    preferred = [agent for agent in agents if persona_by_agent.get(agent.id) in preferred_personas]
    if preferred:
        return random.choice(preferred)
    return random.choice(agents)


def _takeaway_line(item: FinanceNewsItem) -> str:
    tags = set(item.tags or [])
    if "宏观" in tags:
        return "这条消息更像宏观定价变量，先影响风险偏好，再逐步传到板块和个股。"
    if "业绩" in tags:
        return "这类消息真正能走多远，最后取决于订单、利润和指引有没有兑现。"
    if "A股" in tags:
        return "A股这边先看它能不能带起板块一致性，而不是只拉一两个情绪标的。"
    if "美股" in tags or "港股" in tags:
        return "海外市场更容易先给估值反馈，但要看有没有跨市场共振。"
    if "科技" in tags:
        return "科技线最怕只剩概念不剩兑现，后面一定会回到产能、订单和资本开支。"
    if "金融" in tags:
        return "金融线通常更吃预期差，关键是市场会不会把它理解成系统性信号。"
    if "地产" in tags:
        return "地产相关消息最要看政策强度和销售端有没有真正跟上。"
    return "这条新闻不是不能看，而是要看它能不能继续被资金、政策或业绩二次确认。"


def _watch_line(item: FinanceNewsItem, persona: dict[str, str]) -> str:
    tags = set(item.tags or [])
    if "宏观" in tags:
        detail = "接下来重点盯利率、汇率和大宗资产有没有联动。"
    elif "业绩" in tags:
        detail = "接下来重点盯订单、利润率和管理层指引有没有进一步验证。"
    elif "A股" in tags:
        detail = "接下来重点盯板块联动、量能和龙头能不能带起第二梯队。"
    elif "美股" in tags or "港股" in tags:
        detail = "接下来重点盯指数、龙头权重和外围风险资产的同步反应。"
    elif "科技" in tags or "新能源" in tags:
        detail = "接下来重点盯产业链传导、订单侧反馈和高弹性标的扩散。"
    else:
        detail = "接下来重点盯有没有新的确认信号，而不是只看标题热度。"
    return f"{persona['watch']} {detail}"


def _risk_line(item: FinanceNewsItem, persona: dict[str, str]) -> str:
    tags = set(item.tags or [])
    extra = ""
    if "A股" in tags or "港股" in tags or "美股" in tags:
        extra = "如果明天只是高开一下、量能却接不上，很可能就是情绪先透支。"
    elif "宏观" in tags:
        extra = "如果后续数据和正式表态不跟，这类宏观叙事往往会快速回落。"
    elif "业绩" in tags:
        extra = "如果没有后续报表验证，消息面热度很难长期支撑估值。"
    return f"{persona['risk']} {extra}".strip()


def _question_line(item: FinanceNewsItem, persona: dict[str, str]) -> str:
    tags = set(item.tags or [])
    if "A股" in tags:
        return "你们会优先盯龙头、指数，还是先等板块扩散确认？"
    if "业绩" in tags:
        return "你们会把它当成利润兑现的开始，还是仍然只算故事催化？"
    return persona["question"]


def _render_post(item: FinanceNewsItem, agent: User, session: Session) -> tuple[str, str, list[str], str]:
    persona = _persona_for_agent(session, agent.id)
    board_name = item.board_name or DEFAULT_BOARD_NAME
    tags = list(dict.fromkeys((item.tags or [])[:5])) or ["财经"]
    title = f"{item.title}｜{persona['suffix']}"
    summary = item.summary or "这条新闻还在早期发酵阶段，先抓最关键的变量。"
    content = (
        f"【新闻】{item.title}\n"
        f"【来源】{item.source_name}\n"
        f"【链接】{item.link or item.source_url}\n\n"
        f"【我先说结论】\n{persona['opening']} {_takeaway_line(item)}\n\n"
        f"【看到的核心】\n{summary[:140]}\n\n"
        f"【我会继续盯】\n{_watch_line(item, persona)}\n\n"
        f"【风险边界】\n{_risk_line(item, persona)}\n\n"
        f"【抛个问题】\n{_question_line(item, persona)}"
    )
    return title[:180], content, tags, board_name


def _choose_news_to_post(session: Session, rows: list[FinanceNewsItem], agent_ids: list[int]) -> tuple[list[FinanceNewsItem], str]:
    max_age = timedelta(minutes=max(10, int(PGC_MAX_NEWS_AGE_MINUTES)))
    first_seen_window = timedelta(minutes=10)
    now = _utcnow()
    recent_since = now - timedelta(minutes=max(int(PGC_TOPIC_COOLDOWN_MINUTES), int(PGC_AGENT_COOLDOWN_MINUTES), int(PGC_QUIET_MIN_POST_INTERVAL_MINUTES), 180))
    recent_posts = _recent_pgc_posts(session, agent_ids, recent_since)
    if _posting_throttled(now, recent_posts):
        return [], "throttled_by_interval"

    eligible: list[FinanceNewsItem] = []
    topic_since = now - timedelta(minutes=max(10, int(PGC_TOPIC_COOLDOWN_MINUTES)))
    topic_recent_posts = [post for post in recent_posts if post.created_at >= topic_since]
    for row in rows:
        if row.status == "posted":
            continue
        if row.published_at:
            if now - row.published_at > max_age:
                continue
        else:
            if row.first_seen_at and now - row.first_seen_at > first_seen_window:
                continue
        if _is_topic_overheated(row, topic_recent_posts):
            continue
        eligible.append(row)

    if not eligible:
        return [], "no_v2_eligible_news"

    eligible.sort(key=_candidate_priority, reverse=True)
    return eligible[: max(1, int(PGC_POSTS_PER_TICK))], "ok"


def run_pgc_news_tick(session: Session) -> dict:
    ensure_board(session, DEFAULT_BOARD_NAME)
    agents = ensure_pgc_agents(session)
    candidates = fetch_news_candidates()
    if not candidates:
        return {"ok": True, "created": 0, "reason": "no_news_fetched"}
    rows = upsert_news_items(session, candidates)
    agent_ids = [agent.id for agent in agents if agent.id]
    selected, reason = _choose_news_to_post(session, rows, agent_ids)
    if not selected:
        return {"ok": True, "created": 0, "reason": reason}

    recent_since = _utcnow() - timedelta(minutes=max(int(PGC_TOPIC_COOLDOWN_MINUTES), int(PGC_AGENT_COOLDOWN_MINUTES), 180))
    recent_posts = _recent_pgc_posts(session, agent_ids, recent_since)
    created = []
    for row in selected:
        agent = _choose_agent_for_news(session, agents, row, recent_posts, _utcnow())
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
        recent_posts.insert(0, post)

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
                "persona": _persona_for_agent(session, agent.id)["label"],
            }
        )
    return {"ok": True, "created": len(created), "items": created}
