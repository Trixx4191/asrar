"""
Asrār — Hooks  (PreToolUse / PostToolUse)
backend/core/hooks.py

Mirrors Claude Code's hooks system: small, registered functions that run
immediately before or after a tool executes, and can approve, block, or
flag what happens — instead of every tool call being trusted by default.

PreToolUse hooks run before the tool's real implementation. If any hook
blocks, the tool never runs — the block reason is handed back to the model
as the tool's "result" so it can explain to the user, ask for what's
missing, or adjust its plan. This is the actual enforcement layer: e.g. a
multi-file project can't be created until a plan exists for it.

PostToolUse hooks run after the tool's real implementation, with the
result available. They can't undo what already happened, but they review
the outcome — flagging failures into the execution log so a conversation's
behavior is auditable instead of "the model said it worked, so it worked."

Add new hooks by writing a function with the same signature and appending
it to PRE_TOOL_HOOKS / POST_TOOL_HOOKS below.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import memory


@dataclass
class HookResult:
    blocked: bool = False
    reason: str = ""   # why a PreToolUse hook blocked the call
    note: str = ""      # feedback a PostToolUse hook wants appended to the result
    action_id: str = ""  # set when a block created a pending_actions row for human approval


# ─────────────────────────────────────────────────────────────
# PreToolUse hooks
# ─────────────────────────────────────────────────────────────

def _gate_project_creation_on_plan(name: str, args: dict, conversation_id: str | None) -> HookResult:
    """Multi-file projects need a plan first.

    A single-file drop isn't really 'multi-step' work, so it's exempt —
    this targets exactly the failure mode we saw: jumping straight to
    create_project on a vague, multi-file ask with no real plan behind it.
    """
    if name != "create_project":
        return HookResult()

    files_dict = args.get("files_dict") or {}
    if len(files_dict) <= 1:
        return HookResult()

    if not conversation_id:
        return HookResult()  # standalone use, no conversation to check a plan against

    if memory.get_plan(conversation_id):
        return HookResult()

    return HookResult(
        blocked=True,
        reason=(
            "This project has multiple files — call update_plan first to lay out the steps "
            "(e.g. scaffold the folder, write each file, add a README), then call "
            "create_project again once the plan exists."
        ),
    )


_CONFIRM_COMMAND_PATTERNS = [
    "rm ", "del ", "rmdir", "format", "reg delete", "reg add",
    "netsh", "iptables", "pip install", "npm install -g",
    "choco install", "apt install", "apt-get install",
    "systemctl", "service ", "sudo ",
]


def _gate_destructive_commands(name: str, args: dict, conversation_id: str | None) -> HookResult:
    """Shell commands that modify system state need explicit user
    confirmation first — the same permission gate Claude Code applies to
    risky bash commands before running them.

    Blocking here also files a pending_actions row (when we have a
    conversation to attach it to) so the UI can offer a real Approve/Deny
    action tied to this exact command, instead of the only path to
    'confirmed' being the model's own self-report after a chat exchange."""
    if name != "run_command":
        return HookResult()
    if args.get("confirmed"):
        return HookResult()

    command = (args.get("command") or "")
    if any(p in command.lower() for p in _CONFIRM_COMMAND_PATTERNS):
        action_id = ""
        if conversation_id:
            action_id = memory.create_pending_action(
                conversation_id,
                "run_command",
                {"command": command, "timeout": args.get("timeout", 30)},
                reason="Needs explicit user approval before running.",
            )
        return HookResult(
            blocked=True,
            action_id=action_id,
            reason=(
                f"This command needs the user's explicit approval before running:\n\n"
                f"```bash\n{command}\n```\n\n"
                f"Ask the user to confirm, then call run_command again with confirmed=true."
            ),
        )
    return HookResult()


PRE_TOOL_HOOKS: list[Callable[[str, dict, str | None], HookResult]] = [
    _gate_project_creation_on_plan,
    _gate_destructive_commands,
]


def run_pre_tool_hooks(name: str, args: dict, conversation_id: str | None) -> HookResult:
    """Run all PreToolUse hooks; the first one to block wins."""
    for hook in PRE_TOOL_HOOKS:
        result = hook(name, args, conversation_id)
        if result.blocked:
            return result
    return HookResult()


# ─────────────────────────────────────────────────────────────
# PostToolUse hooks
# ─────────────────────────────────────────────────────────────

_ERROR_MARKERS = ("error:", "❌", "⛔", "blocked")


def _flag_tool_failures(name: str, args: dict, result: str, conversation_id: str | None) -> HookResult:
    """Don't just trust that a tool call worked — check its own result text
    for failure markers and flag it, so failures are visible in the
    execution log distinct from successes."""
    low = (result or "").lower()
    if any(m in low for m in _ERROR_MARKERS):
        return HookResult(note="did not fully succeed")
    return HookResult()


POST_TOOL_HOOKS: list[Callable[[str, dict, str, str | None], HookResult]] = [
    _flag_tool_failures,
]


def run_post_tool_hooks(name: str, args: dict, result: str, conversation_id: str | None) -> HookResult:
    """Run all PostToolUse hooks; returns the first hook with feedback, if any."""
    for hook in POST_TOOL_HOOKS:
        r = hook(name, args, result, conversation_id)
        if r.note:
            return r
    return HookResult()
