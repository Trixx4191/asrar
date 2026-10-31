"""Persist and stream Kimi Code ACP sessions with scoped permission tickets."""
import asyncio
import contextlib
import json
from pathlib import Path
import uuid

from core import memory
from core.preferences import load_preferences
from core.execution_policy import EFFICIENCY_INSTRUCTIONS
from core.codex_chat import session_for, save_session
from core.kimi_bridge import bridge, spawn, ACPClient, KimiError


async def stream_chat(message, conversation_id, model=None, context_files=None):
    if conversation_id in bridge.active:
        yield {"error": "This conversation already has a running task"}
        return
    active = {}
    bridge.active[conversation_id] = active
    client = None
    prompt = None
    text = ""
    started = False
    finished = False
    queue = asyncio.Queue(maxsize=256)
    tools = {}
    try:
        async with bridge._auth_lock:
            if bridge.login:
                raise KimiError("Finish or cancel the pending Kimi sign-in first")
            if not bridge.account():
                raise KimiError("Sign in with Kimi in Accounts before sending a task")
            settings = load_preferences()
            scope = bridge.scope()
        workspace = Path(settings.workspace)
        existing = session_for(conversation_id)
        if existing and (existing["runtime"] != "kimi" or existing["workspace"] != settings.workspace or existing["account_scope"] != scope):
            raise KimiError("This chat belongs to another workspace or account connection. Start a new chat.")
        if not existing and memory.get_messages_full(conversation_id):
            raise KimiError("This chat uses another connection. Start a new chat to use Kimi.")
        for value in context_files or []:
            path = Path(value).expanduser()
            path = (path if path.is_absolute() else workspace / path).resolve()
            if not path.is_relative_to(workspace) or not path.is_file():
                raise KimiError("Attached context must be an existing file inside the selected workspace")
            message += "\nAttached workspace file: " + str(path.relative_to(workspace))

        async def on_message(peer, data):
            params = data.get("params") or {}
            if data["method"] == "session/update" and "id" not in data:
                if params.get("sessionId") == active.get("session_id") and active.get("prompting"):
                    await queue.put(params.get("update") or {})
                return
            if "id" not in data:
                return
            if data["method"] == "session/request_permission" and params.get("sessionId") == active.get("session_id") and active.get("prompting"):
                call = params.get("toolCall") or {}
                options = params.get("options") or []
                # Read-only never grants edits or shell execution; uncertain tools are denied.
                if settings.access_mode == "read-only" and call.get("kind") not in {"read", "search", "think"}:
                    await peer.send({"id": data["id"], "result": {"outcome": {"outcome": "cancelled"}}})
                    return
                if call.get("kind") in {"read", "search", "think"}:
                    option = next((item for item in options if item.get("kind") == "allow_once"), None)
                    if option:
                        await peer.send({"id": data["id"], "result": {"outcome": {"outcome": "selected", "optionId": option["optionId"]}}})
                        return
                ticket = uuid.uuid4().hex
                bridge.approvals[ticket] = {"conversation_id": conversation_id, "client": peer,
                                            "id": data["id"], "options": options}
                await queue.put({"approval_required": True, "action_id": ticket, "runtime": "kimi",
                                 "command": call.get("title") or "Kimi tool action", "cwd": settings.workspace,
                                 "reason": "Kimi Code requests permission for this action"})
                return
            # No filesystem/terminal capabilities are advertised: unknown requests fail closed.
            await peer.send({"id": data["id"], "error": {"code": -32601, "message": "Unsupported client operation"}})

        client = ACPClient(await spawn("acp", cwd=settings.workspace), on_message)
        active["client"] = client
        if active.get("stopped"):
            raise KimiError("Task stopped")
        await client.request("initialize", {"protocolVersion": 1, "clientCapabilities": {},
                                            "clientInfo": {"name": "asrar", "version": "0.14.0"}})
        if existing:
            active["session_id"] = existing["thread_id"]
            result = await client.request("session/load", {"sessionId": existing["thread_id"], "cwd": settings.workspace, "mcpServers": []})
        else:
            result = await client.request("session/new", {"cwd": settings.workspace, "mcpServers": []})
            active["session_id"] = result["sessionId"]
            save_session(conversation_id, result["sessionId"], settings.workspace, scope, runtime="kimi")
        catalog = result.get("models") or {}
        if catalog:
            bridge._catalog = [{"id": m["modelId"], "displayName": m.get("name", m["modelId"]),
                                "isDefault": m["modelId"] == catalog.get("currentModelId")}
                               for m in catalog.get("availableModels", [])]
        chosen = model or settings.kimi_model
        if chosen:
            await client.request("session/set_model", {"sessionId": active["session_id"], "modelId": chosen})
        instructions = ("You are Asrar, a local coding assistant. Work in the selected workspace. "
                        "Read and search files without asking for confirmation. Use native permission requests for changes and commands; do not ask for the same approval again in chat. "
                        f"Selected access: {settings.access_mode}. "
                        + ("Do not run commands or modify files. " if settings.access_mode == "read-only" else "")
                        + ("Do not use network tools. " if not settings.network_access else "")
                        + EFFICIENCY_INSTRUCTIONS + settings.instructions)
        memory.add_message(conversation_id, "user", message)
        started = True
        active["prompting"] = True
        prompt = asyncio.create_task(client.request("session/prompt", {
            "sessionId": active["session_id"], "prompt": [{"type": "text", "text": instructions + "\n\n" + message}]}, timeout=None))
        async def completed():
            try:
                await prompt
            except (Exception, asyncio.CancelledError):
                pass
            await queue.put(None)
        completion = asyncio.create_task(completed())
        yield {"meta": True, "model": chosen or "Kimi Code", "provider": "kimi", "task_type": "coding",
               "reason": "Kimi account · official Kimi Code runtime"}
        try:
            while (update := await queue.get()) is not None:
                if update.get("approval_required"):
                    yield update
                    continue
                kind = update.get("sessionUpdate")
                if kind in {"agent_message_chunk", "agent_thought_chunk"}:
                    content = update.get("content") or {}
                    if content.get("type") == "text":
                        if kind == "agent_message_chunk":
                            text += content.get("text", "")
                            yield {"token": content.get("text", "")}
                        else:
                            yield {"thinking": content.get("text", "")}
                elif kind in {"tool_call", "tool_call_update"}:
                    key = update.get("toolCallId")
                    if key not in tools:
                        tools[key] = {"id": key, "name": update.get("title") or update.get("kind") or "Kimi tool", "args": {}}
                        yield {"tool_start": tools[key]["name"], "tool_call_id": key, "args": {}}
                    if update.get("status") in {"completed", "failed"}:
                        yield {"tool_result": tools[key]["name"], "tool_call_id": key,
                               "success": update["status"] == "completed", "preview": json.dumps(update.get("content") or [], ensure_ascii=False)[:12000]}
            result = await prompt
            if result.get("stopReason") not in {"end_turn", "cancelled"}:
                raise KimiError("Kimi ended before completing the task (" + str(result.get("stopReason")) + ")")
            if active.get("stopped") or result.get("stopReason") == "cancelled":
                text += "\n\nTask stopped."
        finally:
            completion.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await completion
        memory.add_message(conversation_id, "assistant", text, model=chosen or "Kimi Code", task_type="coding", tool_calls=list(tools.values()))
        finished = True
        yield {"done": True, "response": text, "model_used": chosen or "Kimi Code", "task_type": "coding", "tool_calls": list(tools.values())}
    except (KimiError, OSError, TimeoutError, KeyError) as exc:
        note = "Task stopped." if active.get("stopped") else "Task failed: " + str(exc)
        if started:
            text += "\n\n" + note
            memory.add_message(conversation_id, "assistant", text, model="Kimi Code", task_type="coding")
            finished = True
        if not active.get("stopped"):
            yield {"error": str(exc)}
        yield {"done": True, "response": text or note, "model_used": "Kimi Code", "task_type": "coding", "tool_calls": list(tools.values())}
    finally:
        if prompt and not prompt.done():
            prompt.cancel()
        if client:
            await client.close()
        if prompt:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await prompt
        if started and not finished:
            memory.add_message(conversation_id, "assistant", text + "\n\nTask stopped.", model="Kimi Code", task_type="coding")
        for ticket, entry in list(bridge.approvals.items()):
            if entry["conversation_id"] == conversation_id:
                bridge.approvals.pop(ticket, None)
        bridge.active.pop(conversation_id, None)
