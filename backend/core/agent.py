"""
Asrār — Main Agent Loop
Receives a task, classifies it, picks the best model,
calls the right tools, and returns a response.
"""

import sys
import os
import asyncio
import json
from pathlib import Path
from datetime import datetime

# Path setup
ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "backend" / "core"))
sys.path.insert(0, str(ROOT / "backend"))

from core.classifier import classify_task
from core.router import route
from providers import get_provider
from providers.base import Message
from tools import web, files, shell, diagnosis

LOG_PATH = ROOT / "logs" / "actions.log"
ASRAR_SYSTEM = (ROOT / "ASRAR.md").read_text(encoding="utf-8")[:2000]

SYSTEM_PROMPT = f"""You are Asrār (أسرار), a powerful local agentic AI assistant.
You run on the user's PC and can browse the web, work with files, run shell commands, and diagnose PC issues.

Your personality: direct, calm, efficient. Never pad responses. Always say which tool you used.
When you run a tool, briefly say what you did and what you found.
Before running shell commands, always summarize what the command does and ask for confirmation.

Core rules:
- Never delete files without explicit confirmation
- Never run destructive shell commands without a warning
- If unsure, ask. If confident, act and report.
- Always tell the user which AI model handled their task.
"""


def _log(entry: dict):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({**entry, "ts": datetime.now().isoformat()}) + "\n")


async def _call_tool(tool_name: str, args: dict) -> str:
    """Route tool calls to the right function."""
    try:
        if tool_name == "web_search":
            return await web.search_and_summarize(args.get("query", ""))

        elif tool_name == "fetch_page":
            r = await web.fetch_page(args.get("url", ""))
            return r.page_content or r.error or "No content"

        elif tool_name == "read_file":
            r = files.read_file(args.get("path", ""))
            return r.content or r.error or "Empty file"

        elif tool_name == "write_file":
            r = files.write_file(args.get("path", ""), args.get("content", ""), args.get("overwrite", False))
            return f"File written: {r.path}" if r.success else r.error

        elif tool_name == "list_dir":
            r = files.list_dir(args.get("path", "."))
            return r.content or r.error

        elif tool_name == "run_command":
            check = shell.check_command(args.get("command", ""))
            if check["blocked"]:
                return f"❌ Blocked: This command is on the safety blocklist."
            if check["needs_confirm"] and not args.get("confirmed", False):
                return f"⚠️ This command needs confirmation: `{args['command']}`\nReply 'yes run it' to proceed."
            r = await shell.run(args.get("command", ""))
            out = r.stdout or r.stderr or "(no output)"
            return f"Exit {r.returncode}:\n{out}"

        elif tool_name == "diagnose_system":
            mode = args.get("mode", "overview")
            if mode == "full":
                r = await diagnosis.full_diagnosis()
            elif mode == "processes":
                r = await diagnosis.top_processes()
            elif mode == "crashes":
                r = await diagnosis.read_crash_logs()
            else:
                r = await diagnosis.system_overview()
            return r.report or r.error or "No data"

        else:
            return f"Unknown tool: {tool_name}"

    except Exception as e:
        return f"Tool error ({tool_name}): {str(e)}"


async def run_task(
    user_input: str,
    history: list[Message] | None = None,
    force_model: str | None = None,
) -> dict:
    """
    Main entry point.
    Returns: { response, model_used, task_type, tool_calls }
    """
    history = history or []

    # 1. Classify + route
    decision = route(user_input, force_model=force_model)

    # 2. Get provider
    provider = get_provider(decision.selected_model["provider"])

    if not provider.is_available():
        # Try fallback chain
        for fallback_key in decision.fallback_chain:
            from core.router import load_registry
            registry = load_registry()
            fb_model = registry["models"][fallback_key]
            provider = get_provider(fb_model["provider"])
            if provider.is_available():
                decision.selected_model = fb_model
                decision.selected_model_key = fallback_key
                break
        else:
            return {
                "response": "No available models. Please check your API keys in .env",
                "model_used": None,
                "task_type": decision.task_type,
                "tool_calls": [],
            }

    # 3. Build context with tool awareness
    tool_context = _build_tool_context(decision.task_type, user_input)
    messages = history + [Message(role="user", content=user_input + tool_context)]

    # 4. Call model
    resp = await provider.complete(
        messages=messages,
        model_id=decision.selected_model["id"],
        system=SYSTEM_PROMPT,
        max_tokens=2048,
    )

    tool_calls = []

    if not resp.success:
        response_text = f"Model error: {resp.error}"
    else:
        response_text = resp.content
        # Parse and execute any tool calls embedded in response
        tool_calls = await _execute_embedded_tools(response_text)
        if tool_calls:
            # Append tool results and re-call model for final answer
            tool_summary = "\n".join(f"[{t['tool']}]: {t['result']}" for t in tool_calls)
            followup = messages + [
                Message(role="assistant", content=response_text),
                Message(role="user", content=f"Tool results:\n{tool_summary}\n\nNow give your final answer.")
            ]
            final_resp = await provider.complete(
                messages=followup,
                model_id=decision.selected_model["id"],
                system=SYSTEM_PROMPT,
            )
            if final_resp.success:
                response_text = final_resp.content

    _log({
        "task": user_input[:100],
        "task_type": decision.task_type,
        "model": decision.selected_model["display_name"],
        "tools": [t["tool"] for t in tool_calls],
        "success": resp.success,
    })

    return {
        "response": response_text,
        "model_used": decision.selected_model["display_name"],
        "task_type": decision.task_type,
        "routing_reason": decision.reason,
        "tool_calls": tool_calls,
    }


def _build_tool_context(task_type: str, user_input: str) -> str:
    """Append tool-use instructions to prompt based on task type."""
    hints = {
        "web_research": "\n\n[Tool available: web_search — use it to find current information]",
        "pc_diagnosis": "\n\n[Tool available: diagnose_system — use it to check system health]",
        "shell_automation": "\n\n[Tool available: run_command — propose the command and confirm before running]",
        "document_work": "\n\n[Tool available: read_file / write_file — ask for file path if needed]",
    }
    return hints.get(task_type, "")


async def _execute_embedded_tools(response: str) -> list[dict]:
    """
    Look for tool call markers in model response and execute them.
    Models are prompted to use format: [TOOL:tool_name|arg=value]
    """
    import re
    pattern = r"\[TOOL:(\w+)\|([^\]]*)\]"
    matches = re.findall(pattern, response)

    results = []
    for tool_name, args_str in matches:
        args = {}
        for pair in args_str.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                args[k.strip()] = v.strip()
        result = await _call_tool(tool_name, args)
        results.append({"tool": tool_name, "args": args, "result": result[:1000]})

    return results


# CLI test
if __name__ == "__main__":
    async def main():
        test_tasks = [
            "What are the top processes using CPU on my machine?",
            "Search the web for latest news on open source AI models",
            "List files in the current directory",
        ]
        for task in test_tasks:
            print(f"\n{'='*60}")
            print(f"Task: {task}")
            result = await run_task(task)
            print(f"Model : {result['model_used']}")
            print(f"Type  : {result['task_type']}")
            print(f"Answer: {result['response'][:300]}...")

    asyncio.run(main())
