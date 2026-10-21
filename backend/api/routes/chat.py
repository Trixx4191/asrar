"""
Route: /chat
POST /chat        — full response (uses agent loop, returns when done)
POST /chat/stream — SSE streaming via shared run_task_stream (no duplicated loop)
POST /chat/approve — human-gated destructive command approval
"""

from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Literal
import json
import traceback
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.agent import run_task, run_task_stream
from core import memory
from tools import shell

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=100000)
    conversation_id: str | None = None
    history: list[dict] = []
    force_model: str | None = None
    runtime: Literal["api", "codex", "claude", "kimi", "muse"] = "api"
    codex_model: str | None = None
    reasoning_effort: str | None = None
    claude_model: str | None = None
    claude_effort: str | None = None
    kimi_model: str | None = None
    muse_model: str | None = None
    muse_effort: str | None = None
    context_files: list[str] = Field(default_factory=list, max_length=8)


def check_conversation(conversation_id, runtime):
    from core.codex_chat import session_for
    if conversation_id and not memory.get_conversation(conversation_id):
        raise HTTPException(404, "Conversation not found")
    session = session_for(conversation_id) if conversation_id else None
    if session and session["runtime"] != runtime:
        raise HTTPException(409, "This chat uses another connection. Start a new chat to switch connections.")


class ChatResponse(BaseModel):
    response: str
    model_used: str | None
    task_type: str
    routing_reason: str
    tool_calls: list[dict]
    conversation_id: str


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Non-streaming chat — shared agent loop."""
    check_conversation(req.conversation_id, req.runtime)
    if req.runtime != "api":
        raise HTTPException(400, "Use /chat/stream for subscription tasks and interactive approvals")
    conversation_id = req.conversation_id or memory.create_conversation(
        title=memory.auto_title(req.message)
    )
    history = memory.get_messages(conversation_id)
    memory.add_message(conversation_id, "user", req.message)

    result = await run_task(
        user_input=req.message,
        history=history,
        force_model=req.force_model,
        conversation_id=conversation_id,
    )

    memory.add_message(
        conversation_id,
        "assistant",
        result["response"],
        model=result["model_used"],
        task_type=result["task_type"],
        tool_calls=result["tool_calls"],
    )

    return ChatResponse(**result, conversation_id=conversation_id)


class ApprovalRequest(BaseModel):
    conversation_id: str
    action_id: str
    approved: bool


class ApprovalResponse(BaseModel):
    status: str
    output: str | None = None


@router.post("/approve", response_model=ApprovalResponse)
async def approve_action(req: ApprovalRequest):
    """Resolve a pending destructive-command approval."""
    pending = memory.get_pending_action(req.action_id)
    if not pending or pending["conversation_id"] != req.conversation_id:
        return ApprovalResponse(status="not_found")

    if pending["status"] != "pending":
        return ApprovalResponse(status="already_resolved")

    if not req.approved:
        memory.resolve_pending_action(req.action_id, "denied")
        command = pending["args"].get("command", "")
        note = f"❌ Command was not approved:\n```bash\n{command}\n```"
        memory.add_message(req.conversation_id, "assistant", note)
        return ApprovalResponse(status="denied", output=note)

    resolved = memory.resolve_pending_action(req.action_id, "approved")
    if not resolved:
        return ApprovalResponse(status="already_resolved")

    tool = pending.get("tool") or "run_command"
    args = dict(pending.get("args") or {})
    args["confirmed"] = True

    if tool == "run_command":
        command = args.get("command", "")
        timeout = args.get("timeout", 30)
        result = await shell.run(command, timeout=timeout, override=True)
        output = result.stdout or result.stderr or "(no output)"
        status_line = "✅" if result.success else f"❌ exit {result.returncode}"
        note = f"✅ Approved and ran:\n```bash\n{command}\n```\n{status_line}\n{output}"
    else:
        # Re-dispatch gated tools (pip_install, npm_install, git_commit, kill_process, …)
        from core.agent import _call_tool
        from core.hooks import approved_action
        with approved_action(tool, args):
            output = await _call_tool(tool, args, conversation_id=req.conversation_id)
        note = f"✅ Approved `{tool}`:\n{output}"

    memory.add_message(req.conversation_id, "assistant", note)
    return ApprovalResponse(status="approved", output=note)


@router.post("/stream")
async def chat_stream(req: ChatRequest, x_asrar_client: str | None = Header(default=None)):
    """SSE streaming — thin wrapper around shared run_task_stream."""
    check_conversation(req.conversation_id, req.runtime)
    if req.runtime != "api" and x_asrar_client != "desktop-v1":
        raise HTTPException(403, "Use the Asrār desktop client")

    async def generator():
        conversation_id = req.conversation_id or memory.create_conversation(
            title=memory.auto_title(req.message)
        )
        yield f"data: {json.dumps({'conversation_id': conversation_id})}\n\n"

        if req.runtime == "muse":
            from core.muse_chat import stream_chat as muse_stream
            async for event in muse_stream(req.message, conversation_id, req.muse_model, req.muse_effort, req.context_files):
                yield f"data: {json.dumps(event)}\n\n"
            return

        if req.runtime == "kimi":
            from core.kimi_chat import stream_chat as kimi_stream
            async for event in kimi_stream(req.message, conversation_id, req.kimi_model, req.context_files):
                yield f"data: {json.dumps(event)}\n\n"
            return

        if req.runtime == "claude":
            from core.claude_chat import stream_chat as claude_stream
            async for event in claude_stream(req.message, conversation_id, req.claude_model, req.claude_effort, req.context_files):
                yield f"data: {json.dumps(event)}\n\n"
            return

        if req.runtime == "codex":
            from core.codex_chat import stream_chat
            async for event in stream_chat(req.message, conversation_id, req.codex_model, req.reasoning_effort, req.context_files):
                yield f"data: {json.dumps(event)}\n\n"
            return

        history = memory.get_messages(conversation_id)
        memory.add_message(conversation_id, "user", req.message)

        final_text = ""
        final_tool_calls: list = []
        model_used = None
        task_type = None

        try:
            async for event in run_task_stream(
                user_input=req.message,
                history=history,
                force_model=req.force_model,
                conversation_id=conversation_id,
            ):
                if "error" in event and len(event) == 1:
                    yield f"data: {json.dumps(event)}\n\n"
                    return

                if event.get("token"):
                    final_text += event["token"]

                if event.get("done"):
                    final_tool_calls = event.get("tool_calls") or []
                    if event.get("response") is not None:
                        final_text = event["response"] or final_text
                    model_used = event.get("model_used") or model_used
                    task_type = event.get("task_type") or task_type
                    # Commit before announcing completion so the client can
                    # immediately refresh the conversation without a race.
                    memory.add_message(
                        conversation_id, "assistant", final_text,
                        model=model_used, task_type=task_type, tool_calls=final_tool_calls,
                    )

                if event.get("meta"):
                    model_used = event.get("model") or model_used
                    task_type = event.get("task_type") or task_type

                yield f"data: {json.dumps(event)}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'error': str(e), 'trace': traceback.format_exc()[:500]})}\n\n"
            return

    return StreamingResponse(generator(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })
