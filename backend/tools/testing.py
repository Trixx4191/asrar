"""
Tool: Testing & verification
backend/tools/testing.py

Gives the agent a way to check its own work instead of assuming an edit
succeeded because the write call returned without error. Two tools:

  run_tests(path, command=None)
      Auto-detects the project's test runner (pytest, npm/yarn/pnpm,
      go test, cargo test, maven, gradle) from files in the directory,
      runs it, and parses a pass/fail summary out of the output. An
      explicit `command` overrides detection for anything unusual.

  execute_code(code, language)
      Runs a short snippet in a temp file and returns stdout/stderr/exit
      code. For when there's no project test suite, or the agent just
      wants to sanity-check a function it wrote before finishing.

Both shell out via tools.shell.run(), so they inherit its timeout and
blocklist handling rather than reimplementing process management.
"""

from __future__ import annotations

import json as _json
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from tools import shell


@dataclass
class TestResult:
    success: bool
    framework: str | None = None
    command: str | None = None
    passed: int | None = None
    failed: int | None = None
    total: int | None = None
    summary: str = ""
    output_tail: str = ""
    error: str | None = None


@dataclass
class ExecResult:
    success: bool
    language: str = ""
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    error: str | None = None


# ─────────────────────────────────────────────────────────────
# Test-runner detection
# ─────────────────────────────────────────────────────────────

def detect_test_command(path: str = ".") -> tuple[str | None, str | None]:
    """Return (framework, command) for the given project root, or (None, None)
    if nothing recognizable is there."""
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        return None, None

    names = {f.name for f in p.iterdir()}

    has_pytest_marker = (
        "pytest.ini" in names
        or "conftest.py" in names
        or list(p.glob("test_*.py"))
        or list(p.glob("*_test.py"))
        or list(p.glob("tests/test_*.py"))
        or list(p.glob("tests/*_test.py"))
    )
    if not has_pytest_marker and (p / "pyproject.toml").exists():
        try:
            has_pytest_marker = "pytest" in (p / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    if has_pytest_marker:
        return "pytest", "python -m pytest -q"

    if "package.json" in names:
        try:
            pkg = _json.loads((p / "package.json").read_text(encoding="utf-8", errors="replace"))
            if "test" in (pkg.get("scripts") or {}):
                if (p / "pnpm-lock.yaml").exists():
                    return "npm", "pnpm test"
                if (p / "yarn.lock").exists():
                    return "npm", "yarn test"
                return "npm", "npm test --silent"
        except (OSError, ValueError):
            pass

    if "go.mod" in names:
        return "go", "go test ./..."

    if "Cargo.toml" in names:
        return "cargo", "cargo test"

    if "pom.xml" in names:
        return "maven", "mvn -q -B test"

    if "build.gradle" in names or "build.gradle.kts" in names:
        return "gradle", "./gradlew test --console=plain"

    return None, None


_PYTEST_SUMMARY_RE = re.compile(r"(\d+) passed(?:, (\d+) failed)?(?:, (\d+) error)?", re.I)
_PYTEST_FAILED_ONLY_RE = re.compile(r"(\d+) failed", re.I)
_JEST_SUMMARY_RE = re.compile(r"Tests:\s+(?:(\d+) failed, )?(?:(\d+) skipped, )?(\d+) passed, (\d+) total", re.I)


def _parse_summary(framework: str, output: str) -> dict:
    if framework == "pytest":
        m = _PYTEST_SUMMARY_RE.search(output)
        if m:
            passed = int(m.group(1) or 0)
            failed = int(m.group(2) or 0)
            errored = int(m.group(3) or 0)
            return {"passed": passed, "failed": failed + errored, "total": passed + failed + errored}
        fm = _PYTEST_FAILED_ONLY_RE.search(output)
        if fm:
            failed = int(fm.group(1))
            return {"passed": 0, "failed": failed, "total": failed}
    elif framework == "npm":
        m = _JEST_SUMMARY_RE.search(output)
        if m:
            failed = int(m.group(1) or 0)
            passed = int(m.group(3))
            total = int(m.group(4))
            return {"passed": passed, "failed": failed, "total": total}
    return {}


async def run_tests(path: str = ".", command: str | None = None, timeout: int = 180) -> TestResult:
    framework = None
    if not command:
        framework, command = detect_test_command(path)
        if not command:
            return TestResult(
                success=False,
                error=(
                    f"No recognized test suite found under '{path}' (checked for pytest, "
                    "npm/yarn/pnpm test script, go test, cargo test, maven, gradle). "
                    "If tests exist under a different runner, pass an explicit `command`. "
                    "If there genuinely is no test suite, use execute_code for a quick sanity "
                    "check instead, or say so explicitly rather than skipping verification."
                ),
            )

    safe_path = path.replace("'", "'\\''")
    full_command = command if path in (".", "", None) else f"cd '{safe_path}' && {command}"
    result = await shell.run(full_command, timeout=timeout, override=True)

    output = f"{result.stdout or ''}\n{result.stderr or ''}"
    stats = _parse_summary(framework or "", output) if framework else {}
    tail = output.strip()[-2000:]

    if stats:
        summary = f"{stats.get('passed', 0)} passed, {stats.get('failed', 0)} failed"
    else:
        summary = "tests passed" if result.success else "tests failed (see output)"

    return TestResult(
        success=result.success,
        framework=framework,
        command=command,
        passed=stats.get("passed"),
        failed=stats.get("failed"),
        total=stats.get("total"),
        summary=summary,
        output_tail=tail,
        error=None if result.success else (result.error or f"exit code {result.returncode}"),
    )


# ─────────────────────────────────────────────────────────────
# Ad-hoc snippet execution
# ─────────────────────────────────────────────────────────────

_LANG_EXT = {"python": ".py", "node": ".js", "bash": ".sh", "ruby": ".rb"}
_LANG_RUNNERS = {
    "python": lambda f: f"python3 '{f}'",
    "node": lambda f: f"node '{f}'",
    "bash": lambda f: f"bash '{f}'",
    "ruby": lambda f: f"ruby '{f}'",
}


async def execute_code(code: str, language: str = "python", timeout: int = 30) -> ExecResult:
    language = (language or "python").lower().strip()
    if language not in _LANG_RUNNERS:
        return ExecResult(
            success=False,
            language=language,
            error=f"Unsupported language '{language}'. Use one of: {', '.join(_LANG_RUNNERS)}.",
        )

    tmp_dir = Path(tempfile.gettempdir()) / "asrar_exec"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_file = tmp_dir / f"snippet_{uuid.uuid4().hex[:8]}{_LANG_EXT[language]}"
    tmp_file.write_text(code, encoding="utf-8")

    try:
        result = await shell.run(_LANG_RUNNERS[language](str(tmp_file)), timeout=timeout, override=True)
        return ExecResult(
            success=result.success,
            language=language,
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode,
            error=None if result.success else (result.error or f"exit code {result.returncode}"),
        )
    finally:
        try:
            tmp_file.unlink(missing_ok=True)
        except OSError:
            pass


# ─────────────────────────────────────────────────────────────
# Shared helper — used by agent.py to decide what counts as
# "code that should be verified before the agent calls itself done"
# ─────────────────────────────────────────────────────────────

CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".c", ".cpp",
    ".cc", ".h", ".hpp", ".rb", ".php", ".cs", ".swift", ".kt", ".sh",
}


def is_code_file(path: str) -> bool:
    return Path(path or "").suffix.lower() in CODE_EXTENSIONS
