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
    if "---" not in [line.strip() for line in lines[1:]]:
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
    if not path.resolve().is_relative_to(SKILLS_ROOT.resolve()):
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    metadata = _frontmatter(text)
    name = metadata.get("name", "").strip()
    if not _SAFE_NAME.fullmatch(name) or name != path.parent.name or not metadata.get("description"):
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


def prompt_context(user_input: str, max_chars: int = 8000) -> tuple[str, list[str]]:
    """Expose descriptions cheaply; load full instructions only on explicit invocation.

    Implicit selection is performed by the main model using use_skill, avoiding
    a second classifier request and avoiding loading unrelated playbooks.
    """
    catalog = list_skills()
    lines = ["Available workflow skills (load with use_skill before applying):"]
    used = len(lines[0])
    for skill in catalog:
        line = f"- {skill['name']}: {skill['description'][:240]}"
        if used + len(line) > max_chars:
            lines.append("Additional skills are available through list_skills.")
            break
        lines.append(line)
        used += len(line) + 1
    loaded = []
    for name in dict.fromkeys(re.findall(r"(?<!\w)[$/]([A-Za-z0-9][A-Za-z0-9_-]*)(?![\w/-])", user_input)):
        skill = load_skill(name)
        if skill:
            loaded.append(name)
            lines.append(f"\nExplicitly requested skill: {name}\n{skill['content']}")
    return "\n".join(lines), loaded
