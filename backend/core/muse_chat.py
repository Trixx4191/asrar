"""Translate Muse's authoritative MSP items into persisted Asrar chat events."""
import asyncio
import contextlib
import json
from pathlib import Path
import uuid

from core import memory
from core.codex_chat import session_for, save_session
from core.muse_bridge import bridge, MuseError
from core.preferences import load_preferences


def profile_for(rows, access_mode):
    wanted = {"readonly"} if access_mode == "read-only" else {"askme"}
    for row in rows:
        names = {str(row.get(k, "")).lower().replace("-", "").replace("_", "").replace(" ", "") for k in ("profileId", "label")}
        if row.get("available") and names & wanted:
            return row["profileId"]
    raise MuseError("Muse does not offer the required permission profile. Update Muse Code; access was not relaxed.")


async def stream_chat(message, conversation_id, model=None, effort=None, context_files=None):
    if conversation_id in bridge.active:
        yield {"error": "This conversation already has a running task"}
        return
    active = {}
    bridge.active[conversation_id] = active
    queue = asyncio.Queue(maxsize=512)
    session_id = None
    started = False
    finished = False
    terminal = False
    text = ""
    items = {}
    tools = {}
    model_name = "Muse Code"
    try:
        async with bridge._auth_lock:
            if bridge.login:
                raise MuseError("Finish or cancel pending Muse sign-in before starting a task")
            settings = load_preferences()
            if bridge.process and bridge.network != settings.network_access:
                if len(bridge.active) > 1:
                    raise MuseError("Stop other Muse tasks before changing network permissions")
                await bridge.close()
            if not await bridge.account():
                raise MuseError("Sign in with Muse in Accounts before sending a task")
            scope = bridge.scope()
        if bridge.handshake.get("sandboxAvailability") != "available":
            raise MuseError("Muse cannot confirm sandbox availability on this machine. Update or repair its sandbox before running tasks.")
        existing = session_for(conversation_id)
        if existing and (existing["runtime"] != "muse" or existing["workspace"] != settings.workspace or existing["account_scope"] != scope):
            raise MuseError("This chat belongs to another workspace or account connection. Start a new chat.")
        if not existing and memory.get_messages_full(conversation_id):
            raise MuseError("This chat uses another connection. Start a new chat to use Muse.")
        for value in context_files or []:
            path = Path(value).expanduser()
            path = (path if path.is_absolute() else Path(settings.workspace) / path).resolve()
            if not path.is_relative_to(Path(settings.workspace)) or not path.is_file():
                raise MuseError("Attached context must be an existing file inside the selected workspace")
            message += "\nAttached workspace file: " + str(path.relative_to(settings.workspace))
        chosen = model or settings.muse_model
        effort = effort or settings.muse_effort
        catalog = await bridge.models()
        selected = next((m for m in catalog if m["id"] == chosen), None) if chosen else next((m for m in catalog if m["isDefault"]), None)
        if not selected:
            raise MuseError("The selected Muse model is not available for this account. Refresh models and choose an available model.")
        if effort and effort not in {v["reasoningEffort"] for v in selected["supportedReasoningEfforts"]}:
            raise MuseError("This Muse model does not support the chosen reasoning effort")
        model_name = selected["displayName"]
        profiles = await bridge.request("permissionProfile/list")
        profile = profile_for(profiles.get("profiles", []), settings.access_mode)
        if existing:
            session_id = existing["thread_id"]
            active["session_id"] = session_id
            bridge.listeners[session_id] = queue
            resumed = await bridge.command("session/resume", {"sessionId": session_id, "excludeItems": True, "permissionProfile": profile})
            if resumed.get("session", {}).get("activeTurnId"):
                raise MuseError("This Muse session still has a running task. Stop it in Muse before continuing.")
            await bridge.command("session/setModel", {"sessionId": session_id, "model": {"modelId": selected["id"], "providerId": "meta"}})
        else:
            # Client-minted session identity lets us subscribe before any host push.
            from core.muse_bridge import command_id
            session_id = command_id()
            active["session_id"] = session_id
            bridge.listeners[session_id] = queue
            result = await bridge.command("session/start", {"sessionId": session_id, "workspaceRoot": settings.workspace,
                "workspaceRoots": [settings.workspace], "modelId": selected["id"], "providerId": "meta", "permissionProfile": profile,
                "approvalMode": "onRequest"})
            if result["session"]["sessionId"] != session_id:
                raise MuseError("Muse returned an unexpected session identity")
            save_session(conversation_id, session_id, settings.workspace, scope, runtime="muse")
        await bridge.command("session/setApprovalMode", {"sessionId": session_id, "mode": "onRequest"})
        while not queue.empty():
            queue.get_nowait()  # Resume history is already persisted by Asrar.
        if active.get("stopped"):
            raise MuseError("Task stopped")
        instructions = ("You are Asrar, a local coding assistant. Respect the selected workspace and access controls. "
                        "Explain your work and verify edits.\n" + settings.instructions)
        if settings.web_search == "disabled":
            instructions += "\nDo not use web search tools."
        memory.add_message(conversation_id, "user", message)
        started = True
        params = {"sessionId": session_id, "input": [{"type": "text", "text": instructions + "\n\n" + message}]}
        if effort:
            params["reasoningEffort"] = effort
        result = await bridge.command("turn/start", params)
        active["turn_id"] = result["turnId"]
        if active.get("stopped"):
            await bridge.interrupt(conversation_id)
        yield {"meta": True, "model": model_name, "provider": "muse", "task_type": "coding",
               "reason": "Meta account · Muse Code · " + settings.access_mode}
        while True:
            event = await queue.get()
            method, p = event["method"], event["params"]
            if method == "asrar/disconnected" or method == "view/gap":
                raise MuseError("Muse's event stream was interrupted. The task was stopped; reconnect before continuing.")
            if p.get("turnId") and p["turnId"] != active["turn_id"]:
                continue
            if method in {"item/started", "item/updated", "item/completed"}:
                item = p.get("item") or {}
                key = item.get("itemId")
                old = items.get(key, {})
                if item.get("revision", 0) < old.get("revision", 0):
                    continue
                items[key] = item
                kind = item.get("kind")
                if kind == "toolCall":
                    if key not in tools:
                        args = item.get("args", "{}")
                        try:
                            args = json.loads(args)
                        except (ValueError, TypeError):
                            args = {"raw": args}
                        tools[key] = {"id": key, "name": item.get("tool") or item.get("toolName") or item.get("name") or "Muse tool", "args": args}
                        yield {"tool_start": tools[key]["name"], "tool_call_id": key, "args": args}
                    if method == "item/completed":
                        output = item.get("visibleOutput") or item.get("output") or item.get("text") or ""
                        tools[key].update(result=output, success=item.get("status") not in {"failed", "cancelled"})
                        yield {"tool_result": tools[key]["name"], "tool_call_id": key, "preview": str(output)[:12000], "success": tools[key]["success"]}
            elif method == "item/delta":
                item = items.get(p.get("itemId"), {})
                if item.get("kind") == "agentMessage" and p.get("field", "text") == "text":
                    text += p.get("delta", "")
                    yield {"token": p.get("delta", "")}
            elif method in {"approval/request", "approval/requested", "approval/updated", "userInput/request", "userInput/requested"}:
                is_question = method.startswith("userInput/")
                native_id = p.get("userInputId") if is_question else p.get("approvalId")
                for ticket, previous in list(bridge.approvals.items()):
                    if previous["conversation_id"] == conversation_id and previous.get("native_id") == native_id:
                        bridge.approvals.pop(ticket)
                        yield {"approval_resolved": True, "action_id": ticket}
                ticket = uuid.uuid4().hex
                bridge.approvals[ticket] = {"conversation_id": conversation_id, "kind": "question" if is_question else "approval", "native_id": native_id, "params": p}
                if is_question:
                    yield {"question_required": True, "runtime": "muse", "action_id": ticket, "questions": p.get("questions", [])}
                else:
                    yield {"approval_required": True, "runtime": "muse", "action_id": ticket, "cwd": settings.workspace,
                           "command": p.get("rawArgs") or p.get("toolName") or "Muse tool action", "reason": "Muse Code requests permission for this action"}
            elif method in {"approval/resolved", "userInput/settled"}:
                native_id = p.get("approvalId") or p.get("userInputId")
                for ticket, entry in list(bridge.approvals.items()):
                    if entry["conversation_id"] == conversation_id and entry.get("native_id") == native_id:
                        bridge.approvals.pop(ticket)
                        yield {"approval_resolved": True, "action_id": ticket}
            elif method == "turn/completed":
                terminal = True
                committed = [i for i in items.values() if i.get("kind") == "agentMessage" and i.get("status") == "completed"]
                finals = [i for i in committed if i.get("messagePhase", "finalAnswer") != "commentary"]
                if committed:
                    text = "\n\n".join(i.get("text", "") for i in (finals or committed))
                if p.get("terminal") == "failed":
                    raise MuseError("Muse could not complete the task. Check your account limits and retry.")
                if p.get("terminal") == "cancelled":
                    text += "\n\nTask stopped."
                elif p.get("terminal") != "completed":
                    raise MuseError("Muse ended with an unsupported task status")
                break
        memory.add_message(conversation_id, "assistant", text, model=model_name, task_type="coding", tool_calls=list(tools.values()))
        finished = True
        yield {"done": True, "response": text, "model_used": model_name, "task_type": "coding", "tool_calls": list(tools.values())}
    except (MuseError, OSError, KeyError) as exc:
        note = "Task stopped." if active.get("stopped") else "Task failed: " + str(exc)
        if started:
            text += "\n\n" + note
            memory.add_message(conversation_id, "assistant", text, model=model_name, task_type="coding", tool_calls=list(tools.values()))
            finished = True
        if not active.get("stopped"):
            yield {"error": str(exc)}
        yield {"done": True, "response": text or note, "model_used": model_name, "task_type": "coding", "tool_calls": list(tools.values())}
    finally:
        if session_id and started and not terminal and bridge.process and bridge.process.returncode is None:
            with contextlib.suppress(MuseError, OSError):
                await bridge.interrupt(conversation_id)
                async with asyncio.timeout(5):
                    while True:
                        if (await queue.get())["method"] == "turn/completed":
                            terminal = True
                            break
            if not terminal:
                # Fail closed if the host cannot acknowledge an actual runtime stop.
                await bridge.close()
        if started and not finished:
            memory.add_message(conversation_id, "assistant", text + "\n\nTask stopped.", model=model_name, task_type="coding", tool_calls=list(tools.values()))
        if session_id:
            bridge.listeners.pop(session_id, None)
        for ticket, entry in list(bridge.approvals.items()):
            if entry["conversation_id"] == conversation_id:
                bridge.approvals.pop(ticket, None)
        bridge.active.pop(conversation_id, None)
