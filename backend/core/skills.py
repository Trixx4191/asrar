"""Load repository workflow skills for the agent.

Skills are Markdown playbooks stored under the repository's ``skills/``
directory. They provide reusable workflow guidance; they do not execute
commands or bypass the agent's normal tool and approval checks.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).parent.parent.parent
SKILLS_ROOT = ROOT / "skills"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _frontmatter(text: str) -> dict[str, str]:
    """Read the small YAML-like frontmatter used by repository skills."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}

    values: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _read_skill(path: Path) -> dict | None:
    if not path.is_file() or path.name != "SKILL.md":
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    metadata = _frontmatter(text)
    name = metadata.get("name", "").strip()
    if not name or name != path.parent.name:
        return None

    return {
        "name": name,
        "description": metadata.get("description", "").strip(),
        "path": str(path.relative_to(ROOT)),
        "content": text,
    }


def list_skills() -> list[dict[str, str]]:
    """Return valid skill metadata in stable name order."""
    if not SKILLS_ROOT.is_dir():
        return []

    skills = []
    for path in sorted(SKILLS_ROOT.glob("*/SKILL.md")):
        skill = _read_skill(path)
        if skill:
            skills.append({k: skill[k] for k in ("name", "description", "path")})
    return skills


def load_skill(name: str) -> dict[str, str] | None:
    """Load one valid skill by its directory/name."""
    if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name):
        return None
    return _read_skill(SKILLS_ROOT / name / "SKILL.md")