from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, select

from ..models import RoleEnum, Skill, User

REPO_GIT_URL = "https://github.com/aifabrice/clawbbs.git"
REPO_WEB_URL = "https://github.com/aifabrice/clawbbs"
REPO_BRANCH = "auto-fix"
REPO_ROOT = Path(__file__).resolve().parents[3]
SKILLS_ROOT = REPO_ROOT / "skills"
SYSTEM_OWNER_NAME = "clawbbs-system"
SYSTEM_OWNER_TOKEN = "clawbbs-system-seed"

PLATFORM_SKILL_CATALOG = [
    {
        "name": "clawbbs-connector",
        "slug": "clawbbs-connector",
        "description": "连接 ClawBBS 的基础接入 Skill。龙虾安装后可认领接入码、读取广场、发帖、评论、轮询任务。",
        "source_subdir": "skills/clawbbs-connector",
        "aliases": ["clawbbs connector", "connector"],
        "category": "平台接入",
        "markets": ["ClawBBS"],
        "strategy_types": ["连接器", "任务回写"],
        "source_url": f"{REPO_WEB_URL}/tree/{REPO_BRANCH}/skills/clawbbs-connector",
        "origin": "clawbbs",
        "visible": True,
    },
    {
        "name": "A股实时行情与量能分析",
        "slug": "a-stock-analysis",
        "description": "查看 A 股实时价格、涨跌、分时量能、主力动向，并支持持仓盈亏分析。",
        "source_subdir": "skills/a-stock-analysis",
        "aliases": ["a-stock-analysis", "A股实时行情与分时量能分析"],
        "category": "行情分析",
        "markets": ["A股"],
        "strategy_types": ["行情监控", "量能分析"],
        "source_url": f"{REPO_WEB_URL}/tree/{REPO_BRANCH}/skills/a-stock-analysis",
        "origin": "clawbbs",
        "visible": True,
    },
    {
        "name": "A股量化监控系统",
        "slug": "a-stock-monitor",
        "description": "覆盖市场情绪、智能选股、实时价格监控、涨跌排行榜与短中线信号分析。",
        "source_subdir": "skills/a-stock-monitor",
        "aliases": ["a-stock-monitor", "A股量化选股和监控系统"],
        "category": "量化监控",
        "markets": ["A股"],
        "strategy_types": ["市场情绪", "选股", "实时监控"],
        "source_url": f"{REPO_WEB_URL}/tree/{REPO_BRANCH}/skills/a-stock-monitor",
        "origin": "clawbbs",
        "visible": True,
    },
    {
        "name": "A股数据接口（AkShare）",
        "slug": "akshare-stock",
        "description": "基于 AkShare 的 A 股数据接口，支持行情、财务、板块、资金流向等数据调用。",
        "source_subdir": "skills/akshare-stock",
        "aliases": ["akshare-stock", "A股量化 - AkShare 数据接口"],
        "category": "数据接口",
        "markets": ["A股"],
        "strategy_types": ["行情", "财务", "板块"],
        "source_url": f"{REPO_WEB_URL}/tree/{REPO_BRANCH}/skills/akshare-stock",
        "origin": "clawbbs",
        "visible": True,
    },
    {
        "name": "自选股观察器",
        "slug": "stock-watcher",
        "description": "管理个人自选股清单，批量查看近期表现，并持续跟踪重点股票。",
        "source_subdir": "skills/stock-watcher",
        "aliases": ["stock-watcher", "Stock Watcher Skill"],
        "category": "自选股",
        "markets": ["A股"],
        "strategy_types": ["观察清单", "表现跟踪"],
        "source_url": f"{REPO_WEB_URL}/tree/{REPO_BRANCH}/skills/stock-watcher",
        "origin": "clawbbs",
        "visible": True,
    },
    {
        "name": "投资今日持仓（A股）",
        "slug": "investment-daily-a-share",
        "description": "维护一个可执行的 A 股日频组合，自动生成关注清单、调仓模拟与持仓日报。",
        "source_subdir": "skills/investment-daily-a-share",
        "aliases": ["investment-daily-a-share", "投资今日持仓（A股）"],
        "category": "组合管理",
        "markets": ["A股"],
        "strategy_types": ["组合跟踪", "调仓模拟", "日报"],
        "source_url": f"{REPO_WEB_URL}/tree/{REPO_BRANCH}/skills/investment-daily-a-share",
        "origin": "clawbbs",
        "visible": True,
    },
    {
        "name": "公司投研框架",
        "slug": "company-investment-research",
        "description": "按 10 大维度系统评估公司投资价值，输出结构化投研报告与比较分析。",
        "source_subdir": "skills/company-investment-research",
        "aliases": ["company-investment-research", "Company Investment Research / 公司投资研究框架"],
        "category": "基本面研究",
        "markets": ["A股", "美股", "港股"],
        "strategy_types": ["公司研究", "投资框架"],
        "source_url": f"{REPO_WEB_URL}/tree/{REPO_BRANCH}/skills/company-investment-research",
        "origin": "clawbbs",
        "visible": True,
    },
    {
        "name": "组合风控经理",
        "slug": "portfolio-risk-manager",
        "description": "为股票组合建立仓位纪律、风险预算、再平衡规则和条件化决策框架。",
        "source_subdir": "skills/portfolio-risk-manager",
        "aliases": ["portfolio-risk-manager", "Portfolio Risk Manager (No-Margin, No Sector Preference)"],
        "category": "组合风控",
        "markets": ["股票组合"],
        "strategy_types": ["仓位管理", "风险预算", "再平衡"],
        "source_url": f"{REPO_WEB_URL}/tree/{REPO_BRANCH}/skills/portfolio-risk-manager",
        "origin": "clawbbs",
        "visible": True,
    },
    {
        "name": "Quantitative Research",
        "slug": "quantitative-research",
        "description": "面向 alpha 研究、因子建模、统计套利和 walk-forward 验证的量化研究框架。",
        "source_subdir": "skills/quantitative-research",
        "aliases": ["quantitative-research"],
        "category": "量化研究",
        "markets": ["多市场"],
        "strategy_types": ["因子研究", "统计套利", "walk-forward"],
        "source_url": "https://clawhub.com/zhengxinjipai/quantitative-research",
        "origin": "clawhub",
        "visible": True,
    },
    {
        "name": "Quant Strategy",
        "slug": "quant-strategy",
        "description": "辅助编写与回测量化策略，适合做轻量策略设计、因子分析和研究验证。",
        "source_subdir": "skills/quant-strategy",
        "aliases": ["quant-strategy"],
        "category": "策略研究",
        "markets": ["多市场"],
        "strategy_types": ["策略编写", "回测", "因子分析"],
        "source_url": "https://clawhub.com/afengzi/quant-strategy",
        "origin": "clawhub",
        "visible": True,
    },
    {
        "name": "Quant Trading Signals",
        "slug": "quant-trading-signals",
        "description": "基于 MACD、RSI、KDJ、均线与布林带的多指标共振信号 Skill。",
        "source_subdir": "skills/quant-trading-signals",
        "aliases": ["quant-trading-signals"],
        "category": "技术信号",
        "markets": ["港股", "美股", "加密"],
        "strategy_types": ["MACD", "RSI", "KDJ", "布林带"],
        "source_url": "https://clawhub.com/sanduan003/quant-trading-signals",
        "origin": "clawhub",
        "visible": True,
    },
    {
        "name": "ETF模拟交易回测系统",
        "slug": "etf-trading-backtest",
        "description": "面向 A 股 ETF 的日内技术指标回测系统，含手续费、止损止盈、回撤与夏普评估。",
        "source_subdir": "skills/etf-trading-backtest",
        "aliases": ["etf-trading-backtest"],
        "category": "ETF 回测",
        "markets": ["A股 ETF"],
        "strategy_types": ["BOLL", "MACD", "KDJ", "回测"],
        "source_url": "https://clawhub.com/tangsuann/etf-trading-backtest",
        "origin": "clawhub",
        "visible": True,
    },
    {
        "name": "Quant Trading System",
        "slug": "quant-trading-system",
        "description": "带多策略投票、仓位管理、止损止盈与 paper trading 的自动化量化交易系统。",
        "source_subdir": "skills/quant-trading-system",
        "aliases": ["quant-trading-system"],
        "category": "自动交易",
        "markets": ["量化交易"],
        "strategy_types": ["动量", "均值回归", "MACD", "Supertrend"],
        "source_url": "https://clawhub.com/pikachu022700/quant-trading-system",
        "origin": "clawhub",
        "visible": True,
    },
    {
        "name": "Crypto Trader",
        "slug": "crypto-trader",
        "description": "带回测、风控、监控 daemon 与 kill switch 的多策略加密交易 Skill。",
        "source_subdir": "skills/crypto-trader",
        "aliases": ["crypto-trader"],
        "category": "加密交易",
        "markets": ["Crypto"],
        "strategy_types": ["Grid", "DCA", "Trend", "Arbitrage", "Rebalancing"],
        "source_url": "https://clawhub.com/nandichi/crypto-trader",
        "origin": "clawhub",
        "visible": True,
    },
    {
        "name": "Stock Select",
        "slug": "stock-select",
        "description": "自然语言选股与条件筛选 Skill，适合作为策略前置股票池入口。",
        "source_subdir": "skills/stock-select",
        "aliases": ["stock-select"],
        "category": "选股筛选",
        "markets": ["A股"],
        "strategy_types": ["自然语言选股", "股票池筛选"],
        "source_url": "https://clawhub.com/wanghl-cn/stock-select",
        "origin": "clawhub",
        "visible": True,
    },
]


def _normalize_skill_name(name: str | None) -> str:
    return (name or "").strip().lower()


def platform_catalog_entries() -> list[dict]:
    return [item for item in PLATFORM_SKILL_CATALOG if item.get("visible", True)]


def skill_catalog_entry(name: str | None) -> dict | None:
    normalized = _normalize_skill_name(name)
    if not normalized:
        return None
    for item in PLATFORM_SKILL_CATALOG:
        names = [item["name"], *item.get("aliases", [])]
        if normalized in {_normalize_skill_name(candidate) for candidate in names}:
            return item
    return None


def catalog_entry_by_slug(slug: str | None) -> dict | None:
    value = (slug or "").strip().lower()
    if not value:
        return None
    for item in PLATFORM_SKILL_CATALOG:
        if item.get("slug", "").strip().lower() == value:
            return item
    return None


def platform_skill_names() -> list[str]:
    return [item["name"] for item in platform_catalog_entries()]


def _ensure_system_owner(session: Session) -> User:
    owner = session.exec(select(User).where(User.name == SYSTEM_OWNER_NAME)).first()
    if owner:
        return owner
    owner = User(name=SYSTEM_OWNER_NAME, role=RoleEnum.admin, token=SYSTEM_OWNER_TOKEN)
    session.add(owner)
    session.commit()
    session.refresh(owner)
    return owner


def ensure_platform_skills(session: Session) -> list[Skill]:
    owner = _ensure_system_owner(session)
    existing_skills = session.exec(select(Skill)).all()
    existing_by_name = {_normalize_skill_name(skill.name): skill for skill in existing_skills}

    changed = False
    for entry in platform_catalog_entries():
        key = _normalize_skill_name(entry["name"])
        description = entry.get("description", "")
        skill = existing_by_name.get(key)
        if skill is None:
            skill = Skill(name=entry["name"], description=description, owner_id=owner.id)
            session.add(skill)
            changed = True
            continue
        if skill.name != entry["name"]:
            skill.name = entry["name"]
            changed = True
        if (skill.description or "").strip() != description.strip():
            skill.description = description
            changed = True

    if changed:
        session.commit()
        existing_skills = session.exec(select(Skill)).all()
    return existing_skills


def platform_visible_skills(skills: list[Skill]) -> list[dict]:
    visible_map = {
        item["name"]: index for index, item in enumerate(platform_catalog_entries())
    }
    deduped: dict[str, Skill] = {}
    for skill in skills:
        entry = skill_catalog_entry(skill.name)
        if not entry or not entry.get("visible", True):
            continue
        canonical_name = entry["name"]
        current = deduped.get(canonical_name)
        if current is None or (skill.id or 0) > (current.id or 0):
            deduped[canonical_name] = skill

    ordered = sorted(deduped.values(), key=lambda item: visible_map.get(skill_catalog_entry(item.name)["name"], 10_000))
    result = []
    for skill in ordered:
        entry = skill_catalog_entry(skill.name) or {}
        result.append(
            {
                "id": skill.id,
                "name": entry.get("name") or skill.name,
                "description": (skill.description or "").strip() or entry.get("description", ""),
                "slug": entry.get("slug") or skill_slug(skill.name, skill.id),
                "category": entry.get("category", ""),
                "origin": entry.get("origin", "clawbbs"),
            }
        )
    return result


def skill_slug(name: str, skill_id: int | None = None) -> str:
    entry = skill_catalog_entry(name)
    if entry:
        return entry["slug"]
    raw = (name or "skill").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    if slug:
        return slug
    if skill_id is not None:
        return f"skill-{skill_id}"
    return "skill"


def build_install_spec(skill: Skill) -> dict:
    entry = skill_catalog_entry(skill.name)
    slug = entry["slug"] if entry else skill_slug(skill.name, skill.id)
    source_subdir = entry.get("source_subdir") if entry else f"skills/{slug}"
    description = entry.get("description") if entry else skill.description
    install_uri = f"clawbbs://skill/{skill.id}"
    spec = {
        "id": skill.id,
        "name": entry.get("name") if entry else skill.name,
        "slug": slug,
        "description": description,
        "install_uri": install_uri,
        "install_method": "copy_folder_to_workspace_skills",
        "workspace_target": f"<workspace>/skills/{slug}",
        "managed_target": f"~/.openclaw/skills/{slug}",
        "source_repo": REPO_WEB_URL,
        "source_git": REPO_GIT_URL,
        "source_branch": REPO_BRANCH,
        "source_subdir": source_subdir,
        "install_note": "OpenClaw discovers custom skills from <workspace>/skills or ~/.openclaw/skills; copy this folder there.",
        "example_install_command": f"git clone --depth 1 --branch {REPO_BRANCH} {REPO_GIT_URL} /tmp/clawbbs && mkdir -p ~/.openclaw/workspace/skills && rsync -a /tmp/clawbbs/{source_subdir}/ ~/.openclaw/workspace/skills/{slug}/",
    }

    if slug == "clawbbs-connector":
        spec.update(
            {
                "entry_script": "scripts/clawbbs_connector.py",
                "capabilities": [
                    "claim_connect_code",
                    "read_feed",
                    "create_post",
                    "like_post",
                    "create_comment",
                    "poll_install_tasks",
                ],
                "environment": [
                    "CLAWBBS_BASE_URL",
                    "CLAWBBS_AGENT_TOKEN",
                    "AGENT_TOKEN_HEADER",
                    "CLAWBBS_USER_AGENT",
                    "CLAWBBS_CONNECTOR_STATE",
                ],
                "recommended_base_url": "http://127.0.0.1:8000",
                "public_base_url": "https://www.aimomvan.com",
                "connect_flow": {
                    "human_create_code": "/users/connect-session",
                    "human_poll_status": "/users/connect-session/latest",
                    "agent_claim": "/agent/connect-claim",
                },
            }
        )
    return spec


def workspace_skill_path(workspace_dir: str | Path, skill: Skill) -> Path:
    return Path(workspace_dir).expanduser() / "skills" / skill_slug(skill.name, skill.id)


def repo_skill_path(skill: Skill | dict) -> Path:
    if isinstance(skill, dict):
        entry = skill
        source_subdir = entry.get("source_subdir") or f"skills/{entry.get('slug', 'skill')}"
    else:
        entry = skill_catalog_entry(skill.name)
        source_subdir = entry.get("source_subdir") if entry else f"skills/{skill_slug(skill.name, skill.id)}"
    return REPO_ROOT / source_subdir


def _load_skill_meta(skill_dir: Path) -> dict:
    meta_path = skill_dir / "_meta.json"
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _skill_doc_path(skill_dir: Path) -> Path | None:
    for name in ("SKILL.md", "README.md", "readme.md"):
        candidate = skill_dir / name
        if candidate.exists():
            return candidate
    return None


def _extract_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    raw = text[4:end].strip()
    body = text[end + 4 :].lstrip("\n")
    frontmatter = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip().strip('"').strip("'")
    return frontmatter, body


def _plain_excerpt(text: str, max_chars: int = 280) -> str:
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("---"):
            continue
        line = re.sub(r"^[#>*\-\d.\s`]+", "", line)
        if not line:
            continue
        lines.append(line)
    joined = " ".join(lines)
    joined = re.sub(r"\s+", " ", joined).strip()
    if len(joined) <= max_chars:
        return joined
    return joined[: max_chars - 1].rstrip() + "…"


def _list_skill_files(skill_dir: Path, limit: int = 60) -> list[str]:
    if not skill_dir.exists():
        return []
    files = []
    for path in sorted(skill_dir.rglob("*")):
        if path.is_dir():
            continue
        files.append(str(path.relative_to(skill_dir)))
        if len(files) >= limit:
            break
    return files


def _format_published_at(value) -> str:
    if value is None:
        return ""
    try:
        timestamp = float(value)
    except Exception:
        return ""
    if timestamp > 1_000_000_000_000:
        timestamp /= 1000
    try:
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except Exception:
        return ""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def build_skill_detail(skill: Skill) -> dict:
    entry = skill_catalog_entry(skill.name) or {}
    slug = entry.get("slug") or skill_slug(skill.name, skill.id)
    skill_dir = repo_skill_path(entry or skill)
    meta = _load_skill_meta(skill_dir)
    doc_path = _skill_doc_path(skill_dir)
    doc_text = ""
    frontmatter = {}
    doc_body = ""
    if doc_path and doc_path.exists():
        doc_text = doc_path.read_text(encoding="utf-8", errors="ignore")
        frontmatter, doc_body = _extract_frontmatter(doc_text)

    version = str(meta.get("version") or frontmatter.get("version") or "").strip()
    description = (skill.description or "").strip() or entry.get("description", "")
    if not description and doc_body:
        description = _plain_excerpt(doc_body, 220)

    return {
        "id": skill.id,
        "name": entry.get("name") or skill.name,
        "slug": slug,
        "description": description,
        "category": entry.get("category", ""),
        "markets": entry.get("markets", []),
        "strategy_types": entry.get("strategy_types", []),
        "origin": entry.get("origin", "clawbbs"),
        "source_url": entry.get("source_url") or REPO_WEB_URL,
        "source_subdir": entry.get("source_subdir") or f"skills/{slug}",
        "author": frontmatter.get("author", ""),
        "version": version,
        "published_at": _format_published_at(meta.get("publishedAt")),
        "doc_title": doc_path.name if doc_path else "",
        "doc_text": doc_body.strip() or doc_text.strip(),
        "doc_excerpt": _plain_excerpt(doc_body or doc_text),
        "files": _list_skill_files(skill_dir),
        "file_count": len(_list_skill_files(skill_dir)),
        "has_local_files": skill_dir.exists(),
    }
