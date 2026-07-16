"""
Tool: Codebase search
backend/tools/codesearch.py

Claude Code's Grep/Glob equivalent. Without this, the agent's only way to
learn what's in a project is read_file on a path it already guessed —
which means it edits blind on anything it hasn't been told about by name.

  search_code(pattern, path=".", glob=None)
      Regex search over file contents. Returns file:line:matched-text,
      grouped by file, capped so one huge match set can't blow the
      context window.

  find_files(pattern, path=".")
      Filename search (glob-style: *, **, ?). For "where is the config
      loaded" style questions where the answer is a filename, not a
      line of code.

Both skip the usual noise directories (.git, node_modules, __pycache__,
venv, dist, build, .next) so results are project code, not dependency
trees or build output.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", "venv", ".venv", "env",
    "dist", "build", ".next", ".pytest_cache", "site-packages", ".mypy_cache",
}

# Skip obviously-binary extensions so we don't try to regex-search a .png
BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip",
    ".tar", ".gz", ".exe", ".dll", ".so", ".dylib", ".woff", ".woff2",
    ".ttf", ".eot", ".db", ".sqlite", ".sqlite3", ".pyc", ".class",
}

MAX_FILE_BYTES = 2_000_000  # don't read multi-MB files into memory for a text search


@dataclass
class Match:
    path: str
    line: int
    text: str


@dataclass
class SearchResult:
    success: bool
    matches: list[Match] = field(default_factory=list)
    files_searched: int = 0
    truncated: bool = False
    error: str | None = None


@dataclass
class FindFilesResult:
    success: bool
    paths: list[str] = field(default_factory=list)
    truncated: bool = False
    error: str | None = None


def _iter_files(root: Path):
    for dirpath, dirnames, filenames in _walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fname in filenames:
            yield Path(dirpath) / fname


def _walk(root: Path):
    import os
    yield from os.walk(root)


def search_code(
    pattern: str,
    path: str = ".",
    glob: str | None = None,
    max_matches: int = 100,
    case_sensitive: bool = False,
) -> SearchResult:
    root = Path(path).expanduser().resolve()
    if not root.exists():
        return SearchResult(success=False, error=f"Path does not exist: {path}")

    try:
        flags = 0 if case_sensitive else re.IGNORECASE
        regex = re.compile(pattern, flags)
    except re.error as e:
        return SearchResult(success=False, error=f"Invalid regex pattern: {e}")

    matches: list[Match] = []
    files_searched = 0
    truncated = False

    targets = [root] if root.is_file() else _iter_files(root)

    for f in targets:
        if glob and not fnmatch.fnmatch(f.name, glob):
            continue
        if f.suffix.lower() in BINARY_EXT:
            continue
        try:
            if f.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue

        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeDecodeError):
            continue

        files_searched += 1
        for lineno, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                try:
                    rel = str(f.relative_to(root)) if root.is_dir() else f.name
                except ValueError:
                    rel = str(f)
                matches.append(Match(path=rel, line=lineno, text=line.strip()[:200]))
                if len(matches) >= max_matches:
                    truncated = True
                    break
        if truncated:
            break

    return SearchResult(success=True, matches=matches, files_searched=files_searched, truncated=truncated)


def find_files(pattern: str, path: str = ".", max_results: int = 200) -> FindFilesResult:
    root = Path(path).expanduser().resolve()
    if not root.exists():
        return FindFilesResult(success=False, error=f"Path does not exist: {path}")
    if not root.is_dir():
        return FindFilesResult(success=False, error=f"Not a directory: {path}")

    results: list[str] = []
    truncated = False
    for f in _iter_files(root):
        if fnmatch.fnmatch(f.name, pattern) or fnmatch.fnmatch(str(f.relative_to(root)), pattern):
            results.append(str(f.relative_to(root)))
            if len(results) >= max_results:
                truncated = True
                break

    return FindFilesResult(success=True, paths=sorted(results), truncated=truncated)


def format_search_result(r: SearchResult) -> str:
    if not r.success:
        return f"Error: {r.error}"
    if not r.matches:
        return f"No matches (searched {r.files_searched} files)."

    by_file: dict[str, list[Match]] = {}
    for m in r.matches:
        by_file.setdefault(m.path, []).append(m)

    lines = [f"{len(r.matches)} match(es) in {len(by_file)} file(s) (searched {r.files_searched} total):"]
    for path, ms in by_file.items():
        lines.append(f"\n{path}:")
        for m in ms:
            lines.append(f"  {m.line}: {m.text}")

    if r.truncated:
        lines.append("\n(truncated — narrow the pattern or glob for more complete results)")

    return "\n".join(lines)


def format_find_files_result(r: FindFilesResult) -> str:
    if not r.success:
        return f"Error: {r.error}"
    if not r.paths:
        return "No matching files."
    body = "\n".join(r.paths)
    if r.truncated:
        body += "\n(truncated — narrow the pattern for more complete results)"
    return body
