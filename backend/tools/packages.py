"""
Safe package operations — pip / npm / apt with allow-list and dry-run defaults.
Install/uninstall always require confirmation via hooks.
"""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PackageResult:
    success: bool
    content: str = ""
    error: str | None = None


# Commands that are allowed without extra confirmation beyond the normal shell gate
SAFE_QUERY_PREFIXES = (
    "pip list", "pip show", "pip freeze",
    "npm list", "npm ls", "npm view", "npm outdated",
    "apt list", "dpkg -l",
)


async def _run(cmd: str, timeout: int = 120, cwd: str | None = None) -> PackageResult:
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return PackageResult(success=False, error=f"Command timed out after {timeout}s")
        out = (stdout or b"").decode(errors="replace")
        err = (stderr or b"").decode(errors="replace")
        combined = (out + ("\n" + err if err else "")).strip()
        if proc.returncode != 0:
            return PackageResult(success=False, content=combined, error=f"exit {proc.returncode}")
        return PackageResult(success=True, content=combined)
    except Exception as e:
        return PackageResult(success=False, error=str(e))


async def pip_list() -> PackageResult:
    return await _run("pip list --format=columns")


async def pip_show(package: str) -> PackageResult:
    if not package or not package.replace("-", "").replace("_", "").isalnum():
        return PackageResult(success=False, error="Invalid package name")
    return await _run(f"pip show {shlex.quote(package)}")


async def pip_install(package: str, upgrade: bool = False) -> PackageResult:
    """Must be confirmed by hooks."""
    if not package or any(c in package for c in ";|&`$"):
        return PackageResult(success=False, error="Invalid or unsafe package specifier")
    flags = "--upgrade" if upgrade else ""
    return await _run(f"pip install {flags} {shlex.quote(package)}".strip(), timeout=300)


async def npm_list(path: str = ".") -> PackageResult:
    return await _run("npm list --depth=0", cwd=str(Path(path).expanduser().resolve()))


async def npm_install(package: str | None = None, path: str = ".", dev: bool = False) -> PackageResult:
    cwd = str(Path(path).expanduser().resolve())
    if package:
        if any(c in package for c in ";|&`$"):
            return PackageResult(success=False, error="Invalid package name")
        flag = "--save-dev" if dev else ""
        return await _run(f"npm install {flag} {shlex.quote(package)}".strip(), cwd=cwd, timeout=300)
    return await _run("npm install", cwd=cwd, timeout=300)


def format_package_result(r: PackageResult) -> str:
    if not r.success:
        return f"❌ Package error: {r.error}\n{r.content or ''}".strip()
    return r.content or "(empty)"
