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
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "backend" / "core"))
sys.path.insert(0, str(ROOT / "backend"))

from core.classifier import classify_task
from core.router import route, load_registry
from core import supervisor
from providers import get_provider
from providers.base import Message
from tools import web, files, shell, diagnosis

logger = logging.getLogger("asrar.agent")
LOG_PATH = ROOT / "logs" / "actions.log"
ASRAR_SYSTEM = (ROOT / "ASRAR.md").read_text(encoding="utf-8")[:2000]

SYSTEM_PROMPT = """You are Asrār (أسرار), a powerful agentic AI assistant running on the user's local computer.
You can work with files, create projects, download resources, run commands, and diagnose system issues.

IMPORTANT AGENTIC BEHAVIOR:
- When asked to create, edit, or modify files/projects, use your tools to actually do it — don't just describe what you'd do.
- Use tools for each step. Verify success. Report exact paths and results.
- Think step-by-step and chain tool calls when needed.

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
            "description": "Write content to a file. Creates the file and parent directories if needed.",
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
]


# ─────────────────────────────────────────────────────────────
# Tool executor  (clean dispatch — no regex)
# ─────────────────────────────────────────────────────────────

async def _call_tool(name: str, args: dict) -> str:
    """Execute a tool by name with validated args. Returns a plain string result."""
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
            return r.content if r.success else f"Error: {r.error}"

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

            # First pass without override — shell.run enforces its own blocklist
            # and we layer our confirm gate on top via the 'confirmed' arg
            if not confirmed:
                # Check for risky patterns before running anything
                CONFIRM_PATTERNS = [
                    "rm ", "del ", "rmdir", "format", "reg delete", "reg add",
                    "netsh", "iptables", "pip install", "npm install -g",
                    "choco install", "apt install", "apt-get install",
                    "systemctl", "service ", "sudo ",
                ]
                cmd_lower = command.lower()
                needs_confirm = any(p in cmd_lower for p in CONFIRM_PATTERNS)
                if needs_confirm:
                    return (
                        f"⚠️ This command needs your approval before running:\n\n"
                        f"```bash\n{command}\n```\n\n"
                        f"Confirm by replying 'yes, run it' and I'll proceed."
                    )
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

        else:
            return f"Unknown tool: {name}"

    except KeyError as e:
        return f"Tool error ({name}): missing required argument {e}"
    except Exception as e:
        logger.exception(f"Tool {name} raised")
        return f"Tool error ({name}): {e}"


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
            r = await client.post(f"{provider.base_url}/messages", headers=headers, json=body)
            r.raise_for_status()
            data = r.json()

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
            r = await client.post(url, json=body)
            r.raise_for_status()
            data = r.json()

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
            r = await client.post(f"{api_base}/chat/completions", headers=headers, json=body)
            r.raise_for_status()
            data = r.json()

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

    # 2. Get provider; walk fallback chain if unavailable
    provider = get_provider(decision.selected_model["provider"])
    model_id = decision.selected_model["id"]

    if not provider.is_available():
        registry = load_registry()
        for fallback_key in decision.fallback_chain:
            fb_model = registry["models"][fallback_key]
            fb_provider = get_provider(fb_model["provider"])
            if fb_provider.is_available():
                provider = fb_provider
                model_id = fb_model["id"]
                decision.selected_model = fb_model
                decision.selected_model_key = fallback_key
                break
        else:
            return {
                "response": "❌ No available models. Check your API keys in .env",
                "model_used": None,
                "task_type": decision.task_type,
                "routing_reason": decision.reason,
                "tool_calls": [],
            }

    provider_name = decision.selected_model["provider"]

    # 3. Build message list (OpenAI format internally)
    messages: list[dict] = [
        {"role": m.role, "content": m.content} for m in history
    ]
    messages.append({"role": "user", "content": user_input})

    # 4. Agentic tool loop (max 8 iterations)
    all_tool_calls: list[dict] = []
    max_iterations = 8
    final_text = ""

    for iteration in range(max_iterations):
        text, tool_calls = await _llm_call_with_tools(
            provider=provider,
            model_id=model_id,
            messages=messages,
            system=SYSTEM_PROMPT,
            provider_name=provider_name,
        )

        if not tool_calls:
            # Model is done — no more tool calls
            final_text = text
            break

        # Append assistant turn with tool call declarations
        if provider_name == "anthropic":
            # Anthropic requires tool_use blocks in the assistant content list
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
            messages.append({"role": "assistant", "content": asst_content})
        else:
            # OpenAI format: assistant message carries tool_calls array
            messages.append({
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

        # Execute each tool and append results
        for tc in tool_calls:
            logger.info(f"Tool call: {tc['name']}({tc['args']})")
            result = await _call_tool(tc["name"], tc["args"])

            all_tool_calls.append({
                "tool": tc["name"],
                "args": tc["args"],
                "result": result[:500],
            })

            _log({
                "tool": tc["name"],
                "args": {k: str(v)[:100] for k, v in tc["args"].items()},
                "result_preview": result[:200],
            })

            if provider_name == "anthropic":
                messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tc["id"],
                        "content": result,
                    }],
                })
            elif provider_name == "google":
                messages.append({
                    "role": "tool",
                    "name": tc["name"],
                    "content": result,
                })
            else:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })

    else:
        final_text += f"\n\n⚠️ Reached iteration limit ({max_iterations})."

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
