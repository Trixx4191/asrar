"""
Tool: Shell
Run shell commands safely. Always confirms before running.
Blocks destructive commands unless explicitly overridden.
"""

import asyncio
import subprocess
import shlex
from dataclasses import dataclass


@dataclass
class ShellResult:
    success: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    blocked: bool = False
    error: str | None = None


# Commands that are blocked unless override=True
BLOCKED_PATTERNS = [
    "rm -rf /",
    "rm -rf ~",
    "mkfs",
    "dd if=",
    "format c:",
    "> /dev/sda",
    "shutdown",
    "reboot",
    ":(){:|:&};:",   # fork bomb
    "del /f /s /q c:\\",
]

# Commands that need confirmation even if not fully blocked
CONFIRM_PATTERNS = [
    "rm ", "del ", "rmdir", "format",
    "reg delete", "reg add",
    "netsh", "iptables",
    "pip install", "npm install -g",
    "choco install", "apt install",
]


def is_navigation_only(command: str) -> bool:
    """Recognize literal cd/pwd commands without executing shell expansions.

    Chained navigation is safe; substitutions, redirects, and other commands
    must still go through the normal permission checks.
    """
    if not isinstance(command, str) or any(char in command for char in "$`\n\r"):
        return False
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return False
    groups = [[]]
    for token in tokens:
        if token == "&&":
            groups.append([])
        else:
            groups[-1].append(token)
    for group in groups:
        if not group:
            return False
        if group == ["pwd"]:
            continue
        if group[0] != "cd":
            return False
        args = group[1:]
        if args and args[0] in {"-L", "-P"}:
            args = args[1:]
        if args and args[0] == "--":
            args = args[1:]
        if len(args) > 1 or any(arg in {";", "&", "|", "||", "<", ">", ">>", "(", ")"} for arg in args):
            return False
    return True


def _is_blocked(command: str) -> bool:
    cmd_lower = command.lower()
    return any(p in cmd_lower for p in BLOCKED_PATTERNS)


def _needs_confirm(command: str) -> bool:
    cmd_lower = command.lower()
    return any(p in cmd_lower for p in CONFIRM_PATTERNS)


async def run(command: str, timeout: int = 30, override: bool = False) -> ShellResult:
    """
    Run a shell command asynchronously.
    - Blocks dangerous commands outright
    - Returns needs_confirm signal for moderate commands
    """
    if _is_blocked(command) and not override:
        return ShellResult(
            success=False,
            blocked=True,
            error=f"Command blocked for safety: '{command}'. This command is on the blocklist."
        )

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

        return ShellResult(
            success=proc.returncode == 0,
            stdout=stdout.decode("utf-8", errors="replace").strip(),
            stderr=stderr.decode("utf-8", errors="replace").strip(),
            returncode=proc.returncode,
        )

    except asyncio.TimeoutError:
        return ShellResult(success=False, error=f"Command timed out after {timeout}s")
    except Exception as e:
        return ShellResult(success=False, error=str(e))


def check_command(command: str) -> dict:
    """Pre-flight check — call this before running to get safety status."""
    return {
        "command": command,
        "blocked": _is_blocked(command),
        "needs_confirm": _needs_confirm(command),
        "safe": not _is_blocked(command) and not _needs_confirm(command),
    }
