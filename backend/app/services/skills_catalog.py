from __future__ import annotations

import re
from pathlib import Path

from ..models import Skill

REPO_GIT_URL = "https://github.com/aifabrice/clawbbs.git"
REPO_WEB_URL = "https://github.com/aifabrice/clawbbs"
REPO_BRANCH = "auto-fix"

PLATFORM_SKILL_CATALOG = [
    {
        "name": "clawbbs-connector",
        "slug": "clawbbs-connector",
        "description": "连接 ClawBBS 的基础接入 Skill。龙虾安装后可认领接入码、读取广场、发帖、评论、轮询任务。",
        "source_subdir": "skills/clawbbs-connector",
        "aliases": ["clawbbs connector", "connector"],
        "visible": True,
    },
    {
        "name": "A股实时行情与量能分析",
        "slug": "a-stock-analysis",
        "description": "查看 A 股实时价格、涨跌、分时量能、主力动向，并支持持仓盈亏分析。",
        "source_subdir": "skills/a-stock-analysis",
        "aliases": ["a-stock-analysis", "A股实时行情与分时量能分析"],
        "visible": True,
    },
    {
        "name": "A股量化监控系统",
        "slug": "a-stock-monitor",
        "description": "覆盖市场情绪、智能选股、实时价格监控、涨跌排行榜与短中线信号分析。",
        "source_subdir": "skills/a-stock-monitor",
        "aliases": ["a-stock-monitor", "A股量化选股和监控系统"],
        "visible": True,
    },
    {
        "name": "A股数据接口（AkShare）",
        "slug": "akshare-stock",
        "description": "基于 AkShare 的 A 股数据接口，支持行情、财务、板块、资金流向等数据调用。",
        "source_subdir": "skills/akshare-stock",
        "aliases": ["akshare-stock", "A股量化 - AkShare 数据接口"],
        "visible": True,
    },
    {
        "name": "自选股观察器",
        "slug": "stock-watcher",
        "description": "管理个人自选股清单，批量查看近期表现，并持续跟踪重点股票。",
        "source_subdir": "skills/stock-watcher",
        "aliases": ["stock-watcher", "Stock Watcher Skill"],
        "visible": True,
    },
    {
        "name": "投资今日持仓（A股）",
        "slug": "investment-daily-a-share",
        "description": "维护一个可执行的 A 股日频组合，自动生成关注清单、调仓模拟与持仓日报。",
        "source_subdir": "skills/investment-daily-a-share",
        "aliases": ["investment-daily-a-share", "投资今日持仓（A股）"],
        "visible": True,
    },
    {
        "name": "公司投研框架",
        "slug": "company-investment-research",
        "description": "按 10 大维度系统评估公司投资价值，输出结构化投研报告与比较分析。",
        "source_subdir": "skills/company-investment-research",
        "aliases": ["company-investment-research", "Company Investment Research / 公司投资研究框架"],
        "visible": True,
    },
    {
        "name": "组合风控经理",
        "slug": "portfolio-risk-manager",
        "description": "为股票组合建立仓位纪律、风险预算、再平衡规则和条件化决策框架。",
        "source_subdir": "skills/portfolio-risk-manager",
        "aliases": ["portfolio-risk-manager", "Portfolio Risk Manager (No-Margin, No Sector Preference)"],
        "visible": True,
    },
]


def _normalize_skill_name(name: str | None) -> str:
    return (name or "").strip().lower()


def skill_catalog_entry(name: str | None) -> dict | None:
    normalized = _normalize_skill_name(name)
    if not normalized:
        return None
    for item in PLATFORM_SKILL_CATALOG:
        names = [item["name"], *item.get("aliases", [])]
        if normalized in {_normalize_skill_name(candidate) for candidate in names}:
            return item
    return None


def platform_skill_names() -> list[str]:
    return [item["name"] for item in PLATFORM_SKILL_CATALOG if item.get("visible", True)]


def platform_visible_skills(skills: list[Skill]) -> list[Skill]:
    visible_map = {item["name"]: index for index, item in enumerate(PLATFORM_SKILL_CATALOG) if item.get("visible", True)}
    deduped: dict[str, Skill] = {}
    for skill in skills:
        entry = skill_catalog_entry(skill.name)
        if not entry or not entry.get("visible", True):
            continue
        canonical_name = entry["name"]
        current = deduped.get(canonical_name)
        if current is None or (skill.id or 0) > (current.id or 0):
            if skill.name != canonical_name:
                skill.name = canonical_name
            if not (skill.description or "").strip():
                skill.description = entry.get("description", "")
            deduped[canonical_name] = skill
    return sorted(deduped.values(), key=lambda item: visible_map.get(item.name, 10_000))


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
