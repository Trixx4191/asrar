"""
Asrār — Main Agent Loop  v0.2.0
backend/core/agent.py

What changed from v0.1:
  - Replaced [TOOL:...] text-marker parsing with native LLM tool calls
    (OpenAI function-calling format for Groq / DeepSeek / OpenRouter / Mistral;
     Anthropic tool_use blocks; Google functionDeclarations)
  - Tool execution is now deterministic — no regex, no XML fallback
  - _build_tool_context() removed — tool schemas are sent via the API
  - Streaming still works; tool calls during streaming are collected
    then executed after the stream ends, same as before

Public API (unchanged — chat.py / server.py need no edits):
    await run_task(user_input, history, force_model) -> dict
    SYSTEM_PROMPT  (imported by chat.py stream route)
"""

from __future__ import annotations

import sys
import os
import asyncio
import json
import logging
import contextvars
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "backend" / "core"))
sys.path.insert(0, str(ROOT / "backend"))

from core.classifier import classify_task
from core.router import route, load_registry
from core import supervisor, memory, hooks
from providers import get_provider
from providers.base import Message
from tools import web, files, shell, diagnosis, testing, codesearch

logger = logging.getLogger("asrar.agent")
LOG_PATH = ROOT / "logs" / "actions.log"
ASRAR_SYSTEM = (ROOT / "ASRAR.md").read_text(encoding="utf-8")[:2000]

SYSTEM_PROMPT = """You are Asrār (أسرار), a powerful agentic AI assistant running on the user's local computer.
You can work with files, create projects, download resources, run commands, and diagnose system issues.

IMPORTANT AGENTIC BEHAVIOR:
- When asked to create, edit, or modify files/projects, use your tools to actually do it — don't just describe what you'd do.
- Use tools for each step. Verify success. Report exact paths and results.
- Think step-by-step and chain tool calls when needed.
- For anything that takes more than one tool call (a project with multiple files, multi-step
  research, anything with 3+ distinct steps), call update_plan FIRST to lay out the steps, then keep
  it current: mark a step in_progress when you start it, completed when you finish it. This keeps
  your progress visible instead of the user having to guess what's happening. Skip it for simple,
  single-step asks — it's not needed for those.
- Multi-file create_project calls are enforced: call update_plan before create_project if the
  project will have more than one file, or the call will be blocked.

VERIFY YOUR OWN WORK BEFORE CALLING IT DONE:
- A successful write_file/edit_file call only means the bytes hit disk — it says nothing about
  whether the code actually works. Don't report a coding task as finished on that basis alone.
- After writing or editing code, check it before your final answer: call run_tests (it
  auto-detects pytest/npm/go test/cargo test/etc. in the project directory) if a test suite
  exists, or execute_code for a quick standalone script/function check if it doesn't.
- If a check fails, fix the issue and re-check — don't hand back broken code with an apology
  instead of a fix, unless you're genuinely stuck after a real attempt, in which case say
  exactly what's failing and why.
- If there is truly no way to verify a change (e.g. it depends on external state you don't have
  access to), say so explicitly in your answer rather than silently skipping verification.
- If you finish a turn with unverified code changes, you'll be reminded once to check them —
  treat that as a real requirement, not a formality.

KNOW WHAT YOU'RE EDITING BEFORE YOU EDIT IT:
- Don't guess at a project's structure, existing function names, or where something is defined.
  Use search_code (content search) or find_files (filename search) to look before you edit —
  especially for "fix the bug in X" or "add Y to the existing Z" style requests where you don't
  already know the exact file/line. list_dir + read_file work for small, known scopes; use
  search_code when you're looking for where something is defined or used across a project.

BEFORE CREATING A PROJECT OR NEW FILES:
- If the request is vague (e.g. "create a project", "build me an app", "make a website") and you don't yet know
  what it should contain, ASK first instead of calling a tool. Find out: what the project is for, what
  language/stack to use, and roughly what it should include.
- Never call create_project or write_file with empty, placeholder, or "TODO" content just to produce something.
  Every file you create should have real, working content suited to what was asked.
- It's fine to make reasonable assumptions for small, well-specified asks (e.g. "write a Python script that
  reverses a string" needs no clarification). Use judgment: ambiguous + multi-file scope → ask; narrow + clear → act.

Your personality: Direct, efficient, action-oriented.
Before running shell commands that modify the system, summarize what they do and ask for confirmation.

Rules:
- Never delete files without explicit confirmation.
- Never run destructive commands without a warning.
- Report the exact file paths of anything you create or modify.
- If a tool fails, try an alternative approach and say what you tried.
"""

# ─────────────────────────────────────────────────────────────
# Tool schemas  (OpenAI function-calling format)
# Used by: Groq, DeepSeek, OpenRouter, Mistral, Anthropic*
# *Anthropic gets converted below in _build_anthropic_tools()
# ─────────────────────────────────────────────────────────────

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file on disk. Supports .txt .md .py .js .ts .json .csv .log .yaml .pdf .docx",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or ~ path to the file"},
                    "max_chars": {"type": "integer", "description": "Max characters to return (default 8000)"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write content to a file, creating it (and parent directories) if needed. "
                "For an EXISTING file you're only partially changing, prefer edit_file instead — "
                "write_file replaces the whole file, so it's easy to accidentally drop content "
                "that should have stayed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or ~ path to write"},
                    "content": {"type": "string", "description": "Text content to write"},
                    "overwrite": {"type": "boolean", "description": "Overwrite if file exists (default false)"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Make a precise edit to an EXISTING file by replacing exact text, leaving the "
                "rest of the file untouched. Always prefer this over write_file when you're "
                "changing part of a file rather than creating it from scratch — it can't "
                "accidentally drop unrelated content. old_string must match the file's current "
                "content exactly (including whitespace/indentation) and must be unique in the "
                "file unless replace_all is set; include a line or two of surrounding context "
                "if needed to make it unique. Read the file first if you're not certain of its "
                "exact current content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the existing file to edit"},
                    "old_string": {"type": "string", "description": "Exact text to find and replace"},
                    "new_string": {"type": "string", "description": "Text to replace it with"},
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace every occurrence instead of requiring exactly one match (default false)",
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_project",
            "description": (
                "Create a new project directory with multiple real files in one call. "
                "Do not call this until you know what the project should contain — "
                "files_dict must hold actual file content, not placeholders."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Project folder name"},
                    "base_path": {"type": "string", "description": "Where to create it (default ~/Downloads)"},
                    "files_dict": {
                        "type": "object",
                        "description": "Map of filename → real file content, e.g. {\"main.py\": \"print('hi')\"}. Required, must not be empty.",
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["name", "files_dict"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files and subdirectories at a given path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path (default '.')"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "download_url",
            "description": "Download a file from a URL and save it locally.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to download"},
                    "path": {"type": "string", "description": "Local path to save to (optional)"},
                    "overwrite": {"type": "boolean", "description": "Overwrite if exists (default false)"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information. Returns a summary of top results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_page",
            "description": "Fetch and extract readable text from a web page URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Run a shell command on Linux/WSL. "
                "IMPORTANT: always ask the user for confirmation before running commands "
                "that modify files, install software, or change system state."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"},
                    "confirmed": {
                        "type": "boolean",
                        "description": "Set true only after the user has explicitly approved this command",
                    },
                    "timeout": {"type": "integer", "description": "Seconds before timeout (default 30)"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diagnose_system",
            "description": "Inspect system health: CPU, RAM, disk, top processes, crash logs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["overview", "processes", "crashes", "full"],
                        "description": "What to inspect (default overview)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": (
                "Search file contents for a regex pattern across a directory tree — like grep. "
                "Use this to find where something is defined, called, or referenced before "
                "editing it, instead of guessing. Returns matching file:line:text, grouped by file. "
                "Skips .git/node_modules/__pycache__/venv/dist/build automatically."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search for"},
                    "path": {"type": "string", "description": "Directory (or single file) to search (default '.')"},
                    "glob": {"type": "string", "description": "Only search filenames matching this glob, e.g. '*.py'"},
                    "case_sensitive": {"type": "boolean", "description": "Default false"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": (
                "Find files by name using a glob pattern (e.g. '*.py', '**/test_*.py', "
                "'config.*'). Use this when you know roughly what a file is called but not "
                "where it lives, as opposed to search_code which looks inside file contents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern to match filenames against"},
                    "path": {"type": "string", "description": "Directory to search under (default '.')"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": (
                "Run the project's test suite and report pass/fail results. Auto-detects the "
                "test runner (pytest, npm/yarn/pnpm test, go test, cargo test, maven, gradle) "
                "from files present in the given directory — pass an explicit `command` only if "
                "detection would guess wrong. Use this after writing or editing code, before "
                "telling the user a task is done, whenever the project has a test suite."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Project directory to test (default '.')"},
                    "command": {"type": "string", "description": "Explicit test command, overriding auto-detection"},
                    "timeout": {"type": "integer", "description": "Seconds before timeout (default 180)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_code",
            "description": (
                "Run a short standalone code snippet and return its stdout/stderr/exit code. "
                "Use this to sanity-check a function or script you just wrote when there's no "
                "project test suite to run with run_tests — e.g. to confirm a script actually "
                "runs without errors and produces the expected output before calling it done."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "The code to run"},
                    "language": {
                        "type": "string",
                        "enum": ["python", "node", "bash", "ruby"],
                        "description": "Language runtime to use (default python)",
                    },
                    "timeout": {"type": "integer", "description": "Seconds before timeout (default 30)"},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_plan",
            "description": (
                "Create or update your step-by-step plan for the current task. Call this BEFORE "
                "starting any task that needs more than one tool call or clearly has multiple steps "
                "(e.g. creating a project, multi-file edits, research + write-up). Pass the FULL "
                "current list every time — mark steps 'completed' as you finish them, set the one "
                "you're working on to 'in_progress', and add new steps if you discover more work. "
                "Skip this entirely for single-step asks."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "The full current plan, in order.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string", "description": "What this step does"},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                },
                            },
                            "required": ["content", "status"],
                        },
                    },
                },
                "required": ["items"],
            },
        },
    },
]


# ─────────────────────────────────────────────────────────────
# Tool executor  (clean dispatch — no regex)
# ─────────────────────────────────────────────────────────────

# Set by write_file/edit_file when they touch an existing file, so a diff
# can be surfaced to the UI as a structured event without threading a second
# return value through every _dispatch_tool branch and both call sites.
_last_diff_var: contextvars.ContextVar[dict | None] = contextvars.ContextVar("_last_diff", default=None)


def get_last_diff() -> dict | None:
    """Consume the diff produced by the most recent write_file/edit_file
    call in this request, if any. Returns {'path': ..., 'diff': ...} or None.
    One-shot — calling this clears it, so a stale diff can't leak into the
    next tool call's event."""
    d = _last_diff_var.get()
    _last_diff_var.set(None)
    return d


async def _dispatch_tool(name: str, args: dict, conversation_id: str | None = None) -> str:
    """The actual tool implementations. Called by _call_tool() only after
    PreToolUse hooks have approved the call — assume by this point the
    call is allowed to happen."""
    try:
        if name == "read_file":
            r = files.read_file(args["path"], max_chars=args.get("max_chars", 8000))
            return r.content if r.success else f"Error: {r.error}"

        elif name == "write_file":
            r = files.write_file(
                args["path"],
                args["content"],
                overwrite=args.get("overwrite", False),
            )
            if not r.success:
                return f"Error: {r.error}"
            if r.diff:
                _last_diff_var.set({"path": args["path"], "diff": r.diff})
                return f"{r.content}\n\n--- diff ---\n{r.diff[:3000]}"
            return r.content

        elif name == "edit_file":
            r = files.edit_file(
                args["path"],
                args["old_string"],
                args["new_string"],
                replace_all=args.get("replace_all", False),
            )
            if not r.success:
                return f"Error: {r.error}"
            if r.diff:
                _last_diff_var.set({"path": args["path"], "diff": r.diff})
                return f"{r.content}\n\n--- diff ---\n{r.diff[:3000]}"
            return r.content

        elif name == "create_project":
            r = files.create_project(
                name=args["name"],
                base_path=args.get("base_path"),
                files_dict=args.get("files_dict") or {},
            )
            return r.content if r.success else f"Error: {r.error}"

        elif name == "list_dir":
            r = files.list_dir(args.get("path", "."))
            return r.content if r.success else f"Error: {r.error}"

        elif name == "download_url":
            r = files.download_url(
                url=args["url"],
                path=args.get("path"),
                overwrite=args.get("overwrite", False),
            )
            return r.content if r.success else f"Error: {r.error}"

        elif name == "web_search":
            return await web.search_and_summarize(args["query"])

        elif name == "fetch_page":
            r = await web.fetch_page(args["url"])
            return r.page_content if r.success else f"Error: {r.error}"

        elif name == "run_command":
            command = args["command"]
            confirmed = args.get("confirmed", False)
            # Destructive-command confirmation is enforced by the
            # _gate_destructive_commands PreToolUse hook before we ever get
            # here — by this point either it wasn't risky, or it was approved.
            r = await shell.run(command, timeout=args.get("timeout", 30), override=confirmed)
            if r.blocked:
                return f"❌ Blocked: {r.error}"
            output = r.stdout or r.stderr or "(no output)"
            status = "✅" if r.success else f"❌ exit {r.returncode}"
            return f"{status}\n{output}"

        elif name == "diagnose_system":
            mode = args.get("mode", "overview")
            fn_map = {
                "overview": diagnosis.system_overview,
                "processes": diagnosis.top_processes,
                "crashes": diagnosis.read_crash_logs,
                "full": diagnosis.full_diagnosis,
            }
            fn = fn_map.get(mode, diagnosis.system_overview)
            r = await fn()
            return r.report if r.success else f"Error: {r.error}"

        elif name == "search_code":
            r = codesearch.search_code(
                pattern=args["pattern"],
                path=args.get("path", "."),
                glob=args.get("glob"),
                case_sensitive=args.get("case_sensitive", False),
            )
            return codesearch.format_search_result(r)

        elif name == "find_files":
            r = codesearch.find_files(pattern=args["pattern"], path=args.get("path", "."))
            return codesearch.format_find_files_result(r)

        elif name == "run_tests":
            r = await testing.run_tests(
                path=args.get("path", "."),
                command=args.get("command"),
                timeout=args.get("timeout", 180),
            )
            if r.error and not r.command:
                return f"Error: {r.error}"
            status = "✅" if r.success else "❌"
            header = f"{status} {r.framework or 'tests'} ({r.command}): {r.summary}"
            body = f"\n{r.output_tail}" if r.output_tail else ""
            return header + body

        elif name == "execute_code":
            r = await testing.execute_code(
                code=args["code"],
                language=args.get("language", "python"),
                timeout=args.get("timeout", 30),
            )
            if r.error and not r.stdout and not r.stderr:
                return f"Error: {r.error}"
            status = "✅" if r.success else f"❌ exit {r.returncode}"
            output = r.stdout or ""
            if r.stderr:
                output += f"\n[stderr]\n{r.stderr}"
            return f"{status}\n{output or '(no output)'}"

        elif name == "update_plan":
            raw_items = args.get("items") or []
            cleaned = []
            for it in raw_items:
                content = str(it.get("content", "")).strip()
                status = it.get("status", "pending")
                if status not in ("pending", "in_progress", "completed"):
                    status = "pending"
                if content:
                    cleaned.append({"content": content, "status": status})

            if conversation_id:
                memory.set_plan(conversation_id, cleaned)

            done = sum(1 for it in cleaned if it["status"] == "completed")
            icon = {"completed": "✓", "in_progress": "→", "pending": "☐"}
            lines = [f"{icon[it['status']]} {it['content']}" for it in cleaned]
            return f"Plan updated ({done}/{len(cleaned)} done):\n" + "\n".join(lines)

        else:
            return f"Unknown tool: {name}"

    except KeyError as e:
        return f"Tool error ({name}): missing required argument {e}"
    except Exception as e:
        logger.exception(f"Tool {name} raised")
        return f"Tool error ({name}): {e}"


async def _call_tool(name: str, args: dict, conversation_id: str | None = None) -> str:
    """Execute a tool by name with validated args. Returns a plain string result.

    Wraps the real dispatch with Claude-Code-style hooks:
      - PreToolUse: can block the call entirely before it runs (e.g. a
        multi-file create_project with no plan yet, or an unconfirmed
        destructive shell command).
      - PostToolUse: can't undo what happened, but reviews the result and
        flags failures into the execution log.
    """
    pre = hooks.run_pre_tool_hooks(name, args, conversation_id)
    if pre.blocked:
        if conversation_id:
            memory.log_event(conversation_id, "tool_blocked", {"tool": name, "reason": pre.reason}, model=None)
        return f"⛔ {pre.reason}"

    _last_diff_var.set(None)
    result = await _dispatch_tool(name, args, conversation_id=conversation_id)

    if conversation_id:
        _track_verification_state(name, args, result, conversation_id)

    post = hooks.run_post_tool_hooks(name, args, result, conversation_id)
    if post.note:
        # Surface the flag to the model too, not just the execution log —
        # a note nobody reads doesn't change behavior.
        result = f"{result}\n\n⚠️ {post.note}"
        if conversation_id:
            memory.log_event(conversation_id, "tool_flagged", {"tool": name, "note": post.note}, model=None)

    return result


_TOOL_FAILURE_MARKERS = ("error:", "❌", "⛔")


def _track_verification_state(name: str, args: dict, result: str, conversation_id: str) -> None:
    """Keep verification_state in sync with what just happened, so the
    run_task loop can tell whether unverified code changes are outstanding
    when the model is about to give its final answer."""
    low = (result or "").lower()
    failed = any(marker in low for marker in _TOOL_FAILURE_MARKERS)

    if name in ("write_file", "edit_file") and not failed:
        path = args.get("path", "")
        if testing.is_code_file(path):
            memory.mark_dirty(conversation_id, path)

    elif name == "create_project" and not failed:
        for fname in (args.get("files_dict") or {}):
            if testing.is_code_file(fname):
                memory.mark_dirty(conversation_id, f"{args.get('name', 'project')}/{fname}")

    elif name in ("run_tests", "execute_code"):
        memory.mark_verified(conversation_id, {"tool": name, "success": not failed})


# ─────────────────────────────────────────────────────────────
# Provider-specific tool format converters
# ─────────────────────────────────────────────────────────────

def _to_anthropic_tools(schemas: list[dict]) -> list[dict]:
    """Convert OpenAI function schemas → Anthropic tools format."""
    out = []
    for s in schemas:
        fn = s["function"]
        out.append({
            "name": fn["name"],
            "description": fn["description"],
            "input_schema": fn["parameters"],
        })
    return out


def _to_google_tools(schemas: list[dict]) -> list[dict]:
    """Convert OpenAI function schemas → Google functionDeclarations format."""
    declarations = []
    for s in schemas:
        fn = s["function"]
        declarations.append({
            "name": fn["name"],
            "description": fn["description"],
            "parameters": fn["parameters"],
        })
    return [{"functionDeclarations": declarations}]


# ─────────────────────────────────────────────────────────────
# Core LLM call with native tool support
# Returns (response_text, tool_calls_made)
# ─────────────────────────────────────────────────────────────

async def _llm_call_with_tools(
    provider,
    model_id: str,
    messages: list[dict],   # raw dicts in OpenAI format
    system: str,
    provider_name: str,
) -> tuple[str, list[dict]]:
    """
    Single LLM call. Handles the three API shapes:
      - OpenAI-compatible (Groq, DeepSeek, OpenRouter, Mistral)
      - Anthropic
      - Google

    Returns:
        (text_content, tool_calls)
        tool_calls: list of {"name": str, "args": dict, "id": str}
    """
    import httpx

    if provider_name == "anthropic":
        # ── Anthropic format ──────────────────────────────────
        anth_messages = []
        for m in messages:
            role = m["role"]
            content = m["content"]
            if role == "tool":
                # tool result → Anthropic wants role=user, content=[tool_result block]
                anth_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.get("tool_call_id", ""),
                        "content": content,
                    }]
                })
            else:
                anth_messages.append({"role": role, "content": content})

        body = {
            "model": model_id,
            "max_tokens": 4096,
            "system": system,
            "tools": _to_anthropic_tools(TOOL_SCHEMAS),
            "messages": anth_messages,
        }
        headers = {
            "x-api-key": provider.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(timeout=90) as client:
            try:
                r = await client.post(f"{provider.base_url}/messages", headers=headers, json=body)
                r.raise_for_status()
                data = r.json()
            except httpx.HTTPStatusError as e:
                status = getattr(getattr(e, "response", None), "status_code", "?")
                try:
                    preview = e.response.text[:200]
                except Exception:
                    preview = "(unable to read error body)"
                try:
                    headers = dict(e.response.headers or {})
                except Exception:
                    headers = {}
                ra = headers.get("retry-after") or headers.get("Retry-After")
                msg = f"Provider HTTP {status}: {preview}" + (f" (retry-after={ra})" if ra else "")
                return msg, []

        text = ""
        tool_calls = []
        for block in data.get("content", []):
            if block["type"] == "text":
                text += block["text"]
            elif block["type"] == "tool_use":
                tool_calls.append({
                    "id": block["id"],
                    "name": block["name"],
                    "args": block["input"],
                })
        return text, tool_calls

    elif provider_name == "google":
        # ── Google Gemini format ──────────────────────────────
        contents = []
        # Inject system as first user/model exchange
        contents.append({"role": "user", "parts": [{"text": f"[System]: {system}"}]})
        contents.append({"role": "model", "parts": [{"text": "Understood."}]})

        for m in messages:
            role = "model" if m["role"] == "assistant" else "user"
            content = m["content"]
            if m["role"] == "tool":
                contents.append({
                    "role": "user",
                    "parts": [{"functionResponse": {
                        "name": m.get("name", "tool"),
                        "response": {"result": content},
                    }}]
                })
            else:
                contents.append({"role": role, "parts": [{"text": content}]})

        body = {
            "contents": contents,
            "tools": _to_google_tools(TOOL_SCHEMAS),
            "generationConfig": {"maxOutputTokens": 4096},
        }
        url = f"{provider.base_url}/models/{model_id}:generateContent?key={provider.api_key}"
        async with httpx.AsyncClient(timeout=90) as client:
            try:
                r = await client.post(url, json=body)
                r.raise_for_status()
                data = r.json()
            except httpx.HTTPStatusError as e:
                status = getattr(getattr(e, "response", None), "status_code", "?")
                try:
                    preview = e.response.text[:200]
                except Exception:
                    preview = "(unable to read error body)"
                try:
                    headers = dict(e.response.headers or {})
                except Exception:
                    headers = {}
                ra = headers.get("retry-after") or headers.get("Retry-After")
                msg = f"Provider HTTP {status}: {preview}" + (f" (retry-after={ra})" if ra else "")
                return msg, []

        text = ""
        tool_calls = []
        candidate = data.get("candidates", [{}])[0]
        for part in candidate.get("content", {}).get("parts", []):
            if "text" in part:
                text += part["text"]
            elif "functionCall" in part:
                fc = part["functionCall"]
                tool_calls.append({
                    "id": fc["name"],  # Google has no id; use name
                    "name": fc["name"],
                    "args": fc.get("args", {}),
                })
        return text, tool_calls

    else:
        # ── OpenAI-compatible (Groq, DeepSeek, OpenRouter, Mistral) ──
        all_messages = [{"role": "system", "content": system}] + messages

        headers = {"Authorization": f"Bearer {provider.api_key}", "Content-Type": "application/json"}
        if hasattr(provider, "headers_extra"):
            headers.update(provider.headers_extra)

        body = {
            "model": model_id,
            "messages": all_messages,
            "max_tokens": 4096,
            "tools": TOOL_SCHEMAS,
            "tool_choice": "auto",
        }

        api_base = provider.base_url
        async with httpx.AsyncClient(timeout=90) as client:
            try:
                r = await client.post(f"{api_base}/chat/completions", headers=headers, json=body)
                r.raise_for_status()
                data = r.json()
            except httpx.HTTPStatusError as e:
                status = getattr(getattr(e, "response", None), "status_code", "?")
                try:
                    preview = e.response.text[:200]
                except Exception:
                    preview = "(unable to read error body)"
                try:
                    headers = dict(e.response.headers or {})
                except Exception:
                    headers = {}
                ra = headers.get("retry-after") or headers.get("Retry-After")
                msg = f"Provider HTTP {status}: {preview}" + (f" (retry-after={ra})" if ra else "")
                return msg, []

        choice = data["choices"][0]
        msg = choice["message"]
        text = msg.get("content") or ""
        tool_calls = []
        for tc in msg.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, KeyError):
                args = {}
            tool_calls.append({
                "id": tc["id"],
                "name": tc["function"]["name"],
                "args": args,
            })
        return text, tool_calls


def _is_provider_error_text(text: str) -> bool:
    return isinstance(text, str) and text.strip().startswith("Provider HTTP")


# ─────────────────────────────────────────────────────────────
# Agentic loop
# ─────────────────────────────────────────────────────────────

def _log(entry: dict):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({**entry, "ts": datetime.now().isoformat()}) + "\n")


async def run_task(
    user_input: str,
    history: list[Message] | None = None,
    force_model: str | None = None,
    conversation_id: str | None = None,
) -> dict:
    """
    Main entry point.
    Returns: { response, model_used, task_type, routing_reason, tool_calls }

    If conversation_id is given, routing goes through the supervisor:
    sticky to the same model while a clarifying question is open, fresh
    classification otherwise. Without a conversation_id, this falls back
    to plain per-call routing (e.g. for standalone/CLI use).
    """
    history = history or []

    # 1. Classify + route (supervisor-aware if we have a conversation to track)
    if conversation_id:
        decision = supervisor.decide_route(conversation_id, user_input, force_model=force_model)
    else:
        decision = route(user_input, force_model=force_model)

    registry = load_registry()
    candidate_keys = [decision.selected_model_key] + decision.fallback_chain
    final_text = ""
    all_tool_calls: list[dict] = []
    last_error = None
    success = False

    for model_key in candidate_keys:
        model = registry["models"].get(model_key)
        if not model:
            continue
        try:
            provider = get_provider(model["provider"])
        except Exception:
            continue
        if not provider.is_available():
            continue

        attempt_messages = [{"role": m.role, "content": m.content} for m in history]
        attempt_messages.append({"role": "user", "content": user_input})
        attempt_tool_calls: list[dict] = []
        attempt_text = ""
        attempt_failed = False

        for iteration in range(10):
            text, tool_calls = await _llm_call_with_tools(
                provider=provider,
                model_id=model["id"],
                messages=attempt_messages,
                system=SYSTEM_PROMPT,
                provider_name=model["provider"],
            )

            if _is_provider_error_text(text) and not tool_calls:
                last_error = f"{model['display_name']}: {text}"
                attempt_failed = True
                break

            if not tool_calls:
                if conversation_id and iteration < 9:
                    vstate = memory.get_verification_state(conversation_id)
                    if vstate["dirty"] and not vstate["nudged"]:
                        memory.set_nudged(conversation_id)
                        _log({"verification_nudge": True, "dirty_files": vstate["dirty_files"]})
                        attempt_messages.append({"role": "assistant", "content": text or ""})
                        changed = ", ".join(vstate["dirty_files"][:5]) or "the file(s) you changed"
                        attempt_messages.append({
                            "role": "user",
                            "content": (
                                f"Before you finish: you changed {changed} but haven't verified "
                                "it/them yet. Run run_tests (or execute_code if there's no test "
                                "suite), fix anything that fails, then give your real final "
                                "answer. If there's genuinely no way to verify this, say so "
                                "explicitly instead of skipping it."
                            ),
                        })
                        continue
                attempt_text = text
                break

            if model["provider"] == "anthropic":
                asst_content = []
                if text:
                    asst_content.append({"type": "text", "text": text})
                for tc in tool_calls:
                    asst_content.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": tc["args"],
                    })
                attempt_messages.append({"role": "assistant", "content": asst_content})
            else:
                attempt_messages.append({
                    "role": "assistant",
                    "content": text or "",
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {"name": tc["name"], "arguments": json.dumps(tc["args"])},
                        }
                        for tc in tool_calls
                    ],
                })

            for tc in tool_calls:
                logger.info(f"Tool call: {tc['name']}({tc['args']})")
                result = await _call_tool(tc["name"], tc["args"], conversation_id=conversation_id)
                tool_success = not any(m in result.lower() for m in _TOOL_FAILURE_MARKERS)
                attempt_tool_calls.append({
                    "tool": tc["name"],
                    "args": tc["args"],
                    "result": result[:500],
                    "success": tool_success,
                })
                _log({
                    "tool": tc["name"],
                    "args": {k: str(v)[:100] for k, v in tc["args"].items()},
                    "result_preview": result[:200],
                })

                if model["provider"] == "anthropic":
                    attempt_messages.append({
                        "role": "user",
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": tc["id"],
                            "content": result,
                        }],
                    })
                elif model["provider"] == "google":
                    attempt_messages.append({
                        "role": "tool",
                        "name": tc["name"],
                        "content": result,
                    })
                else:
                    attempt_messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result,
                    })

        if attempt_failed:
            continue

        success = True
        final_text = attempt_text
        all_tool_calls = attempt_tool_calls
        if model_key != candidate_keys[0]:
            decision.reason += f" Fallbacked to '{model['display_name']}' after an earlier model failed."
        decision.selected_model = model
        decision.selected_model_key = model_key
        break

    if not success:
        return {
            "response": f"❌ All selected models failed: {last_error or 'no available models could complete the request.'}",
            "model_used": None,
            "task_type": decision.task_type,
            "routing_reason": decision.reason,
            "tool_calls": [],
        }

    _log({
        "task": user_input[:120],
        "task_type": decision.task_type,
        "model": decision.selected_model["display_name"],
        "tools": [t["tool"] for t in all_tool_calls],
    })

    if conversation_id:
        supervisor.after_response(
            conversation_id,
            decision.selected_model_key,
            decision.selected_model,
            decision.task_type,
            final_text,
            all_tool_calls,
        )

    return {
        "response": final_text,
        "model_used": decision.selected_model["display_name"],
        "task_type": decision.task_type,
        "routing_reason": decision.reason,
        "tool_calls": all_tool_calls,
    }
