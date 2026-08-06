"""
Git tools — status, diff, log, branch, safe commit.
All mutating operations require explicit confirmation via hooks.
"""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GitResult:
    success: bool
    content: str = ""
    error: str | None = None


def _run_git(args: list[str], cwd: str | None = None, timeout: int = 30) -> GitResult:
    try:
        p = subprocess.run(
            ["git"] + args,
            cwd=cwd or ".",
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (p.stdout or "") + (p.stderr or "")
        if p.returncode != 0:
            return GitResult(success=False, content=out.strip(), error=f"git exit {p.returncode}")
        return GitResult(success=True, content=out.strip())
    except subprocess.TimeoutExpired:
        return GitResult(success=False, error="git command timed out")
    except FileNotFoundError:
        return GitResult(success=False, error="git is not installed on this system")
    except Exception as e:
        return GitResult(success=False, error=str(e))


def git_status(path: str = ".") -> GitResult:
    root = Path(path).expanduser().resolve()
    if not (root / ".git").exists() and not any((root / p).exists() for p in ["../.git", "../../.git"]):
        # Still try — git will walk up
        pass
    return _run_git(["status", "--porcelain", "-b"], cwd=str(root))


def git_diff(path: str = ".", staged: bool = False) -> GitResult:
    args = ["diff"]
    if staged:
        args.append("--cached")
    return _run_git(args, cwd=str(Path(path).expanduser().resolve()))


def git_log(path: str = ".", n: int = 10) -> GitResult:
    return _run_git(
        ["log", f"-{n}", "--oneline", "--decorate"],
        cwd=str(Path(path).expanduser().resolve()),
    )


def git_branch(path: str = ".") -> GitResult:
    return _run_git(["branch", "-vv"], cwd=str(Path(path).expanduser().resolve()))


def git_add(path: str, files: list[str] | None = None) -> GitResult:
    args = ["add"]
    if files:
        args.extend(files)
    else:
        args.append("-A")
    return _run_git(args, cwd=str(Path(path).expanduser().resolve()))


def git_commit(path: str, message: str) -> GitResult:
    if not message or not message.strip():
        return GitResult(success=False, error="Commit message is required")
    return _run_git(
        ["commit", "-m", message.strip()],
        cwd=str(Path(path).expanduser().resolve()),
    )


def git_show_file(path: str, rev: str = "HEAD") -> GitResult:
    return _run_git(["show", f"{rev}:{path}"])


def format_git_result(r: GitResult) -> str:
    if not r.success:
        return f"❌ Git error: {r.error}\n{r.content}".strip()
    return r.content or "(empty)"
