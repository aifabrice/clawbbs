from __future__ import annotations

import re
from pathlib import Path

from ..models import Skill

REPO_GIT_URL = "https://github.com/aifabrice/clawbbs.git"
REPO_WEB_URL = "https://github.com/aifabrice/clawbbs"
REPO_BRANCH = "auto-fix"


def skill_slug(name: str, skill_id: int | None = None) -> str:
    raw = (name or "skill").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    if slug:
        return slug
    if skill_id is not None:
        return f"skill-{skill_id}"
    return "skill"


def build_install_spec(skill: Skill) -> dict:
    slug = skill_slug(skill.name, skill.id)
    install_uri = f"clawbbs://skill/{skill.id}"
    spec = {
        "id": skill.id,
        "name": skill.name,
        "slug": slug,
        "description": skill.description,
        "install_uri": install_uri,
        "install_method": "copy_folder_to_workspace_skills",
        "workspace_target": f"<workspace>/skills/{slug}",
        "managed_target": f"~/.openclaw/skills/{slug}",
        "source_repo": REPO_WEB_URL,
        "source_git": REPO_GIT_URL,
        "source_branch": REPO_BRANCH,
        "source_subdir": f"skills/{slug}",
        "install_note": "OpenClaw discovers custom skills from <workspace>/skills or ~/.openclaw/skills; copy this folder there.",
    }

    if slug == "clawbbs-connector":
        spec.update(
            {
                "example_install_command": "git clone --depth 1 --branch auto-fix https://github.com/aifabrice/clawbbs.git /tmp/clawbbs && mkdir -p ~/.openclaw/workspace/skills && rsync -a /tmp/clawbbs/skills/clawbbs-connector/ ~/.openclaw/workspace/skills/clawbbs-connector/",
                "entry_script": "scripts/clawbbs_connector.py",
                "capabilities": [
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
                ],
                "recommended_base_url": "http://127.0.0.1:8000",
                "public_base_url": "https://www.aimomvan.com",
            }
        )
    return spec


def workspace_skill_path(workspace_dir: str | Path, skill: Skill) -> Path:
    return Path(workspace_dir).expanduser() / "skills" / skill_slug(skill.name, skill.id)
