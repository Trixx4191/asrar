"""Claude Agent SDK turns translated to Asrār chat events and local memory."""
from __future__ import annotations

import asyncio
import contextlib
import difflib
import json
import uuid
from pathlib import Path

import anyio

from tools.shell import is_navigation_only
from core import memory
from core.claude_bridge import bridge, sdk_types, executable, sdk_environment, AUTH_HOME, ClaudeError
from core.codex_chat import session_for, save_session
from core.preferences import load_preferences
from providers.configuration import redact_secrets

READ_TOOLS = {"Read", "Glob", "Grep", "TodoWrite", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "AskUserQuestion"}
WRITE_TOOLS = {"Bash", "Edit", "Write", "NotebookEdit"}
FILE_TOOLS = {"Read", "Glob", "Grep", "Edit", "Write", "NotebookEdit"}


def file_path(name, values, workspace):
    if name not in FILE_TOOLS:
        return None
    value = values.get("file_path") or values.get("notebook_path") or values.get("path") or workspace
    path = Path(value).expanduser()
    return (path if path.is_absolute() else Path(workspace) / path).resolve()


def safe_preview(value, limit=30000):
    return redact_secrets(value if isinstance(value, str) else json.dumps(value, default=str))[:limit]


async def run_turn(message, conversation_id, session_id, resume, settings, model, effort, queue, active):
    try:
        await _run_turn(message, conversation_id, session_id, resume, settings, model, effort, queue, active)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        await queue.put({"error": safe_preview(str(exc), 2000) or "Claude Code could not initialize this task"})
    finally:
        active.pop("client", None)


async def _run_turn(message, conversation_id, session_id, resume, settings, model, effort, queue, active):
    """Connect/receive/disconnect in one task, as required by SDK cancel scopes."""
    sdk = sdk_types()
    allowed = READ_TOOLS | (WRITE_TOOLS if settings.access_mode == "workspace-write" else set())
    if settings.web_search != "disabled":
        allowed |= {"WebSearch", "WebFetch"}
    calls, before = {}, {}

    async def emit(event):
        await queue.put(event)

    async def pre_tool(data, tool_id, context):
        name, values = data.get("tool_name", ""), data.get("tool_input", {})
        denial = None
        path = file_path(name, values, settings.workspace)
        if name not in allowed:
            denial = "This tool is unavailable in the selected Asrār access mode"
        elif path and (not path.is_relative_to(Path(settings.workspace)) or path.is_relative_to(AUTH_HOME.resolve())):
            denial = "Tool paths must remain inside the selected workspace and outside private sign-in storage"
        elif name == "Bash" and values.get("dangerouslyDisableSandbox"):
            denial = "Asrār does not permit Claude commands to disable the sandbox"
        if denial:
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": denial}}
        identifier = tool_id or uuid.uuid4().hex
        if identifier not in calls:
            public_args = json.loads(redact_secrets(json.dumps(values, default=str)))
            calls[identifier] = {"name": name, "args": public_args, "result": None}
            await emit({"tool_start": name, "tool_call_id": identifier, "args": public_args})
        if name == "TodoWrite":
            plan = [{"content": item.get("content", ""), "status": item.get("status", "pending")} for item in values.get("todos", [])]
            memory.set_plan(conversation_id, plan)
            await emit({"plan": plan})
        if path and name in {"Edit", "Write", "NotebookEdit"}:
            try:
                before[identifier] = (path, path.read_text() if path.is_file() and path.stat().st_size <= 1024 * 1024 else "")
            except (OSError, UnicodeError):
                pass
        if name == "Bash" and is_navigation_only(values.get("command", "")):
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}
        if name in WRITE_TOOLS | {"WebSearch", "WebFetch", "AskUserQuestion"}:
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": "Review this action in Asrār"}}
        return {}

    async def post_tool(data, tool_id, context):
        if tool_id in before:
            path, previous = before.pop(tool_id)
            try:
                if path.is_file() and path.stat().st_size <= 1024 * 1024:
                    current = path.read_text()
                    diff = "".join(difflib.unified_diff(previous.splitlines(True), current.splitlines(True), fromfile=str(path), tofile=str(path)))
                    if diff:
                        await emit({"diff": True, "path": str(path), "unified_diff": safe_preview(diff, 60000)})
            except (OSError, UnicodeError):
                pass
        return {}

    async def permission(name, values, context):
        if name not in allowed:
            return sdk.PermissionResultDeny(message="Tool is unavailable in this access mode")
        ticket = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        question = name == "AskUserQuestion"
        entry = {"conversation_id": conversation_id, "turn_id": active["turn_id"],
                 "future": future, "kind": "question" if question else "approval"}
        bridge.approvals[ticket] = entry
        try:
            if question:
                questions = [{"id": str(i), "question": item.get("question", ""),
                              "header": item.get("header", ""), "options": item.get("options", [])}
                             for i, item in enumerate(values.get("questions", []))]
                if not questions:
                    return sdk.PermissionResultDeny(message="No questions were supplied")
                entry["question_ids"] = [item["id"] for item in questions]
                await emit({"question_required": True, "runtime": "claude", "action_id": ticket, "questions": questions})
                answers = await future
                updated = {**values, "answers": {values["questions"][i]["question"]: answers[str(i)] for i in range(len(questions))}}
                return sdk.PermissionResultAllow(updated_input=updated)
            await emit({"approval_required": True, "runtime": "claude", "action_id": ticket,
                        "command": values.get("command") or f"{name}: {safe_preview(values, 4000)}",
                        "reason": getattr(context, "title", None) or "Claude Code needs your approval",
                        "cwd": settings.workspace})
            approved = await future
            return sdk.PermissionResultAllow(updated_input=values) if approved else sdk.PermissionResultDeny(message="The user declined this action")
        finally:
            bridge.approvals.pop(ticket, None)
            await emit({"approval_resolved": True, "action_id": ticket})

    options = sdk.ClaudeAgentOptions(
        cli_path=executable(), cwd=settings.workspace, env=sdk_environment(), setting_sources=[],
        settings='{"forceLoginMethod":"claudeai"}', strict_mcp_config=True,
        tools=sorted(allowed), allowed_tools=[], permission_mode="default",
        can_use_tool=permission, include_partial_messages=True,
        model=model, effort=effort, resume=session_id if resume else None,
        session_id=None if resume else session_id,
        system_prompt={"type": "preset", "preset": "claude_code", "append":
                       "You are Asrār, powered by Claude Code. Respect the selected workspace and access mode. "
                       "Explain changes clearly and verify your work.\n" + settings.instructions},
        hooks={"PreToolUse": [sdk.HookMatcher(hooks=[pre_tool])], "PostToolUse": [sdk.HookMatcher(hooks=[post_tool])]},
        sandbox={"enabled": settings.access_mode == "workspace-write", "failIfUnavailable": True,
                 "autoAllowBashIfSandboxed": False, "allowUnsandboxedCommands": False,
                 "filesystem": {"denyRead": [str(AUTH_HOME)], "denyWrite": [str(AUTH_HOME)]},
                 "network": {"allowedDomains": ["*"] if settings.network_access else [],
                             "deniedDomains": [] if settings.network_access else ["*"],
                             "allowAllUnixSockets": False, "allowLocalBinding": False}},
        stderr=lambda _: None,
    )
    final_seen = False
    try:
        async with sdk.ClaudeSDKClient(options=options) as client:
            active["client"] = client
            await client.query(message)
            streamed = ""
            async for event in client.receive_response():
                if isinstance(event, sdk.StreamEvent):
                    raw = event.event
                    if raw.get("type") == "content_block_delta" and raw.get("delta", {}).get("type") == "text_delta":
                        text = raw["delta"].get("text", "")
                        streamed += text
                        await emit({"token": text})
                elif isinstance(event, sdk.AssistantMessage):
                    for block in event.content:
                        if isinstance(block, sdk.TextBlock) and not streamed:
                            await emit({"token": block.text})
                        elif isinstance(block, sdk.ToolUseBlock) and block.id not in calls:
                            public_args = json.loads(redact_secrets(json.dumps(block.input, default=str)))
                            calls[block.id] = {"name": block.name, "args": public_args, "result": None}
                            await emit({"tool_start": block.name, "tool_call_id": block.id, "args": public_args})
                    streamed = ""
                elif isinstance(event, sdk.UserMessage) and isinstance(event.content, list):
                    for block in event.content:
                        if isinstance(block, sdk.ToolResultBlock):
                            call = calls.get(block.tool_use_id, {"name": "Tool", "args": {}})
                            output = safe_preview(block.content)
                            call.update(result=output, success=not block.is_error)
                            calls[block.tool_use_id] = call
                            await emit({"tool_result": call["name"], "tool_call_id": block.tool_use_id,
                                        "preview": output, "success": not block.is_error})
                elif isinstance(event, sdk.ResultMessage):
                    final_seen = True
                    failed = event.is_error or event.subtype != "success"
                    result = getattr(event, "result", None)
                    error = safe_preview(result or getattr(event, "errors", None) or "Claude Code task failed") if failed else None
                    await emit({"claude_result": True, "response": result if not failed else None,
                                "error_message": error, "tool_calls": list(calls.values()),
                                "stopped": active.get("stopped", False)})
                elif type(event).__name__ == "RateLimitEvent":
                    info = getattr(event, "rate_limit_info", None)
                    bridge.rate_limit = {name: getattr(info, name, None) for name in ("status", "resets_at", "rate_limit_type")} if info else None
                    if info and getattr(info, "status", None) == "rejected":
                        await emit({"notice": "Claude subscription limit reached. Wait for the account's reset before retrying."})
            if not final_seen:
                await emit({"error": "Claude Code ended before reporting a completed task"})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        await emit({"error": safe_preview(str(exc), 2000) or "Claude Code could not complete this task"})


async def stream_chat(message, conversation_id, model=None, effort=None, context_files=None):
    if conversation_id in bridge.active:
        yield {"error": "This conversation already has a running Claude task"}
        return
    active = {"turn_id": uuid.uuid4().hex, "stopped": False}
    bridge.active[conversation_id] = active
    queue = asyncio.Queue(maxsize=256)
    text, persisted, started = "", False, False
    model_name = model or "Claude Code"
    tools = []
    try:
        account = await bridge.require_account()
        settings = load_preferences()
        scope = bridge.scope(account)
        existing = session_for(conversation_id)
        if existing and existing["runtime"] != "claude":
            raise ClaudeError("This chat uses another connection. Start a new chat to use Claude Code.")
        if existing and (existing["workspace"] != settings.workspace or existing["account_scope"] != scope):
            raise ClaudeError("This chat belongs to another workspace or Claude account session. Start a new chat.")
        if not existing and memory.get_messages_full(conversation_id):
            raise ClaudeError("This chat uses another connection. Start a new chat to use Claude Code.")
        if context_files:
            paths = []
            for value in context_files:
                path = Path(value).expanduser()
                path = (path if path.is_absolute() else Path(settings.workspace) / path).resolve()
                if (not path.is_relative_to(Path(settings.workspace)) or path.is_relative_to(AUTH_HOME.resolve()) or not path.is_file()):
                    raise ClaudeError("Attached context must be an existing workspace file outside private sign-in storage")
                paths.append(str(path.relative_to(settings.workspace)))
            message += "\n\nAttached workspace files (read these for context):\n" + "\n".join("- " + path for path in paths)
        chosen_model, chosen_effort = model or settings.claude_model, effort or settings.claude_effort
        catalog = await bridge.models()
        if active["stopped"]:
            yield {"done": True, "response": "Task stopped.", "model_used": model_name, "task_type": "coding", "tool_calls": []}
            return
        selected = next((item for item in catalog if item["id"] == chosen_model), None)
        if chosen_model and not selected:
            raise ClaudeError("This Claude model is unavailable. Select the account default or refresh models.")
        if chosen_effort and (not selected or chosen_effort not in [level["reasoningEffort"] for level in selected.get("supportedReasoningEfforts", [])]):
            raise ClaudeError("Choose a supported Claude effort or use the account default")
        model_name = selected["displayName"] if selected else "Claude Code (account default)"
        session_id = existing["thread_id"] if existing else str(uuid.uuid4())
        save_session(conversation_id, session_id, settings.workspace, scope, runtime="claude")
        memory.add_message(conversation_id, "user", message)
        started = True
        active["task"] = asyncio.create_task(run_turn(message, conversation_id, session_id, bool(existing), settings,
                                                     chosen_model, chosen_effort, queue, active))
        def closed(task):
            # A task cancelled before its first instruction never enters its
            # finally block. The callback still wakes the HTTP stream.
            if not task.cancelled():
                task.exception()
            if queue.full():
                queue.get_nowait()
            queue.put_nowait({"claude_closed": True})
        active["task"].add_done_callback(closed)
        yield {"meta": True, "model": model_name, "provider": "claude", "task_type": "coding",
               "reason": "Claude subscription · Claude Code · " + settings.access_mode}
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), 20)
            except asyncio.TimeoutError:
                yield {"heartbeat": True}
                continue
            if event.get("token"):
                text += event["token"]
            if event.get("claude_closed"):
                if active["stopped"] and not persisted:
                    text += "\n\nTask stopped."
                    memory.add_message(conversation_id, "assistant", text, model=model_name, task_type="coding", tool_calls=tools)
                    persisted = True
                    yield {"done": True, "response": text, "model_used": model_name, "task_type": "coding", "tool_calls": tools}
                break
            if event.get("claude_result"):
                tools = event.get("tool_calls", [])
                text = event.get("response") or text
                if event.get("error_message"):
                    text += "\n\nTask failed: " + event["error_message"]
                    yield {"error": event["error_message"]}
                if event.get("stopped"):
                    text += "\n\nTask stopped."
                memory.add_message(conversation_id, "assistant", text or "Task completed without a text reply.",
                                   model=model_name, task_type="coding", tool_calls=tools)
                persisted = True
                yield {"done": True, "response": text, "model_used": model_name, "task_type": "coding", "tool_calls": tools}
            else:
                if event.get("error"):
                    text += "\n\nTask failed: " + event["error"]
                yield event
    except (ClaudeError, OSError, ValueError, asyncio.TimeoutError) as exc:
        yield {"error": safe_preview(str(exc), 2000) or "Claude Code did not respond. Reconnect and try again."}
    finally:
        # Shield cleanup from HTTP disconnects. SDK connect/disconnect itself
        # remains on run_turn's task; cancellation never moves its cancel scopes.
        with anyio.CancelScope(shield=True):
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await bridge.interrupt(conversation_id)
                if active.get("task"):
                    await asyncio.wait_for(asyncio.shield(active["task"]), 8)
        if started and not persisted:
            memory.add_message(conversation_id, "assistant", text + "\n\nTask stopped or interrupted.",
                               model=model_name, task_type="coding", tool_calls=tools)
        for ticket, request in list(bridge.approvals.items()):
            if request["conversation_id"] == conversation_id:
                if not request["future"].done():
                    request["future"].cancel()
                bridge.approvals.pop(ticket, None)
        bridge.active.pop(conversation_id, None)
