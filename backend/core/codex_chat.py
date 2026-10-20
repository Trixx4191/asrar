"""Translate Codex lifecycle events into Asrār's persisted streaming chat."""
import asyncio
import contextlib
import hashlib
import json
from pathlib import Path

from core import memory
from core.codex_bridge import bridge, AUTH_HOME, CodexError
from core.preferences import load_preferences


def session_for(conversation_id):
    with memory._conn() as conn:
        row = conn.execute("SELECT * FROM runtime_sessions WHERE conversation_id=?", (conversation_id,)).fetchone()
        return dict(row) if row else None


def save_session(conversation_id, thread_id, workspace, scope):
    with memory._conn() as conn:
        conn.execute("INSERT OR REPLACE INTO runtime_sessions VALUES (?, 'codex', ?, ?, ?)",
                     (conversation_id, thread_id, workspace, scope))


async def stream_chat(message, conversation_id, model=None, effort=None, context_files=None):
    """One in-flight turn per conversation; cleanup interrupts real execution."""
    if conversation_id in bridge.active:
        yield {"error": "This conversation already has a running task"}
        return
    active = {}
    bridge.active[conversation_id] = active
    thread_id = None
    queue = None
    text = ""
    items = {}
    tools = []
    finished = False
    persisted = False
    model_name = model or "Codex"
    try:
        account = await bridge.account()
        if not account:
            raise CodexError("Connect your ChatGPT account in Accounts before sending a task")
        settings = load_preferences()
        if context_files:
            files = []
            for value in context_files:
                path = Path(value).expanduser()
                path = (path if path.is_absolute() else Path(settings.workspace) / path).resolve()
                if not path.is_relative_to(Path(settings.workspace)) or not path.is_file():
                    raise CodexError("Attached context must be an existing file inside the selected workspace")
                files.append(str(path.relative_to(settings.workspace)))
            message += "\n\nAttached workspace files (read these for context):\n" + "\n".join("- " + name for name in files)
        chosen_model = model or settings.codex_model
        chosen_effort = effort or settings.reasoning_effort
        catalog = await bridge.models()
        if chosen_model:
            selected = next((m for m in catalog if m.get("model", m.get("id")) == chosen_model), None)
            if not selected:
                raise CodexError("The selected model is no longer available. Choose another model in the chat toolbar.")
        else:
            selected = next((m for m in catalog if m.get("isDefault")), catalog[0] if catalog else None)
        if not selected:
            raise CodexError("No Codex models are available for this account")
        chosen_model = selected.get("model") or selected["id"]
        model_name = selected.get("displayName") or chosen_model
        supported = [entry["reasoningEffort"] for entry in selected.get("supportedReasoningEfforts", [])]
        if chosen_effort and chosen_effort not in supported:
            raise CodexError("This model does not support the chosen reasoning effort. Select its default effort.")
        marker = AUTH_HOME / "asrar-session-id"
        if not marker.exists():
            import uuid
            marker.write_text(uuid.uuid4().hex)
            marker.chmod(0o600)
        scope = hashlib.sha256(((account.get("email") or "") + marker.read_text()).encode()).hexdigest()
        existing = session_for(conversation_id)
        history = memory.get_messages_full(conversation_id)
        if existing and (existing["workspace"] != settings.workspace or existing["account_scope"] != scope):
            raise CodexError("This chat belongs to another workspace or account session. Start a new chat to continue.")
        if not existing and history:
            raise CodexError("This chat uses an API connection. Start a new chat to use your ChatGPT subscription.")
        sandbox = "read-only" if settings.access_mode == "read-only" else "workspace-write"
        thread_params = {"cwd": settings.workspace, "model": chosen_model,
                         "approvalPolicy": "untrusted", "sandbox": sandbox,
                         "modelProvider": "openai", "approvalsReviewer": "user",
                         "config": {"web_search": settings.web_search},
                         "developerInstructions": "You are Asrār, a local coding assistant powered by Codex. "
                         "Explain your work clearly, respect the selected workspace, and verify edits when appropriate.\n" + settings.instructions}
        if existing:
            await bridge.request("thread/resume", {**thread_params, "threadId": existing["thread_id"]})
            thread_id = existing["thread_id"]
        else:
            result = await bridge.request("thread/start", thread_params)
            thread_id = result["thread"]["id"]
            save_session(conversation_id, thread_id, settings.workspace, scope)
        active["thread_id"] = thread_id
        queue = asyncio.Queue(maxsize=256)
        bridge.listeners[thread_id] = queue
        policy = {"type": "readOnly"} if sandbox == "read-only" else {
            "type": "workspaceWrite", "writableRoots": [settings.workspace], "networkAccess": settings.network_access}
        # A read-only thread always has restricted network access.
        params = {"threadId": thread_id, "input": [{"type": "text", "text": message}],
                  "model": chosen_model, "cwd": settings.workspace,
                  "approvalPolicy": "untrusted", "sandboxPolicy": policy}
        if chosen_effort:
            params["effort"] = chosen_effort
        memory.add_message(conversation_id, "user", message)
        result = await bridge.request("turn/start", params)
        active["turn_id"] = result["turn"]["id"]
        yield {"meta": True, "model": model_name, "provider": "codex", "task_type": "coding",
               "reason": "ChatGPT subscription · Codex · " + settings.access_mode}
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), 20)
            except asyncio.TimeoutError:
                yield {"heartbeat": True}
                continue
            method, data = event["method"], event.get("params", {})
            if data.get("turnId") and data["turnId"] != active.get("turn_id"):
                continue
            if method == "asrar/disconnected":
                raise CodexError("Codex disconnected. Reconnect in Accounts before retrying.")
            if method == "asrar/notice":
                yield {"notice": data["message"]}
            elif method == "item/agentMessage/delta":
                identifier = data.get("itemId", "message")
                items[identifier] = items.get(identifier, "") + data.get("delta", "")
                text += data.get("delta", "")
                yield {"token": data.get("delta", "")}
            elif method == "turn/plan/updated":
                plan = [{"content": row.get("step", ""), "status": {"inProgress": "in_progress"}.get(row.get("status"), row.get("status"))}
                        for row in data.get("plan", [])]
                memory.set_plan(conversation_id, plan)
                yield {"plan": plan}
            elif method == "turn/diff/updated":
                yield {"codex_diff": data.get("diff", "")}
            elif method.endswith("/requestApproval"):
                yield {"approval_required": True, "action_id": data["approvalId"], "runtime": "codex",
                       "command": data.get("command") or data.get("reason") or "Approve file changes",
                       "reason": data.get("reason"), "cwd": data.get("cwd"),
                       "network": data.get("networkApprovalContext")}
            elif method == "item/tool/requestUserInput":
                yield {"question_required": True, "action_id": data["approvalId"],
                       "questions": data.get("questions", [])}
            elif method == "serverRequest/resolved":
                yield {"approval_resolved": True, "action_id": data.get("approvalId")}
            elif method in {"item/started", "item/completed"}:
                item = data.get("item", {})
                kind, identifier = item.get("type"), item.get("id")
                complete = method == "item/completed"
                if kind == "agentMessage" and complete:
                    # Completed items are authoritative, including servers that
                    # only send an item and omit token deltas.
                    current = item.get("text", "")
                    previous = items.get(identifier, "")
                    items[identifier] = current
                    if not previous and current:
                        text += current
                        yield {"token": current}
                elif kind in {"commandExecution", "fileChange", "mcpToolCall", "webSearch"}:
                    name = {"commandExecution": "Terminal", "fileChange": "File changes",
                            "mcpToolCall": item.get("tool", "Tool"), "webSearch": "Web search"}[kind]
                    args = {"command": item["command"]} if item.get("command") else {}
                    if not complete:
                        yield {"tool_start": name, "tool_call_id": identifier, "args": args}
                    else:
                        output = item.get("aggregatedOutput") or item.get("result") or item.get("status") or "Completed"
                        output = output if isinstance(output, str) else json.dumps(output)
                        success = item.get("status") not in {"failed", "declined"}
                        tools.append({"name": name, "args": args, "result": output[:30000], "success": success})
                        yield {"tool_result": name, "tool_call_id": identifier, "preview": output[:30000], "success": success}
                elif kind == "contextCompaction":
                    yield {"notice": "Codex is compacting this conversation to make room for more work."}
            elif method == "thread/tokenUsage/updated":
                yield {"usage": data.get("tokenUsage")}
            elif method == "error":
                # Codex may recover; the completed turn determines success.
                yield {"notice": (data.get("error") or {}).get("message", "Codex is reconnecting…")}
            elif method == "turn/completed":
                turn = data.get("turn", {})
                finished = True
                if turn.get("status") == "failed":
                    failure = (turn.get("error") or {}).get("message", "Codex task failed")
                    text += "\n\nTask failed: " + failure
                    yield {"error": failure}
                elif turn.get("status") == "interrupted":
                    text += "\n\nTask stopped."
                else:
                    text = "\n\n".join(value for value in items.values() if value) or text
                memory.add_message(conversation_id, "assistant", text or "Task completed without a text reply.",
                                   model=model_name, task_type="coding", tool_calls=tools)
                persisted = True
                yield {"done": True, "response": text, "model_used": model_name, "task_type": "coding", "tool_calls": tools}
                break
    except (CodexError, OSError, ValueError, KeyError) as exc:
        yield {"error": str(exc)}
    finally:
        if not finished:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(bridge.interrupt(conversation_id), 5)
            if text and not persisted:
                memory.add_message(conversation_id, "assistant", text + "\n\nTask interrupted.",
                                   model=model_name, task_type="coding", tool_calls=tools)
        if thread_id:
            bridge.listeners.pop(thread_id, None)
            for ticket, approval in list(bridge.approvals.items()):
                if approval["thread_id"] == thread_id:
                    bridge.approvals.pop(ticket, None)
        bridge.active.pop(conversation_id, None)
