"""Local desktop account controls; never accepts or returns OAuth tokens."""
from typing import Literal
from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel, ConfigDict, ValidationError

from core.codex_bridge import bridge, CodexError
from core.claude_bridge import bridge as claude_bridge, ClaudeError
from core.preferences import load_preferences, save_preferences


async def desktop_client(x_asrar_client: str | None = Header(default=None)):
    # A custom header forces browser preflight; foreign origins cannot submit
    # account/approval mutations using cross-site forms or simple requests.
    if x_asrar_client != "desktop-v1":
        raise HTTPException(403, "Use the Asrār desktop client")


router = APIRouter(dependencies=[Depends(desktop_client)])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["browser", "device"] = "browser"


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str
    action_id: str
    approved: bool


class StopRequest(BaseModel):
    conversation_id: str


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str
    action_id: str
    answers: dict[str, str]


class ClaudeLoginCode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str


async def call(operation):
    try:
        return await operation
    except (CodexError, ClaudeError, OSError, TimeoutError) as exc:
        raise HTTPException(409, str(exc) or "The runtime did not respond. Reconnect and try again.") from exc


@router.get("/claude")
async def claude_status():
    return await claude_bridge.status()


@router.post("/claude/login")
async def claude_login():
    return await call(claude_bridge.begin_login())


@router.post("/claude/login/code")
async def claude_login_code(req: ClaudeLoginCode):
    await call(claude_bridge.submit_code(req.code))
    return {"success": True}


@router.post("/claude/login/cancel")
async def claude_cancel_login():
    await call(claude_bridge.cancel_login())
    return {"success": True}


@router.post("/claude/logout")
async def claude_logout():
    await call(claude_bridge.logout())
    return {"success": True}


@router.post("/claude/reconnect")
async def claude_reconnect():
    if claude_bridge.active:
        raise HTTPException(409, "Stop running Claude tasks before reconnecting")
    claude_bridge._catalog = None
    return await claude_bridge.status()


@router.get("/claude/models")
async def claude_models():
    return {"models": await call(claude_bridge.models())}


@router.post("/claude/approve")
async def claude_approve(req: DecisionRequest):
    await call(claude_bridge.decide(req.conversation_id, req.action_id, req.approved))
    return {"success": True}


@router.post("/claude/answer")
async def claude_answer(req: AnswerRequest):
    await call(claude_bridge.answer(req.conversation_id, req.action_id, req.answers))
    return {"success": True}


@router.post("/claude/stop")
async def claude_stop(req: StopRequest):
    await call(claude_bridge.interrupt(req.conversation_id))
    return {"success": True}


@router.get("/codex")
async def status():
    return await bridge.status()


@router.post("/codex/login")
async def login(req: LoginRequest):
    return await call(bridge.begin_login(req.method == "device"))


@router.post("/codex/login/cancel")
async def cancel():
    await call(bridge.cancel_login())
    return {"success": True}


@router.post("/codex/logout")
async def logout():
    await call(bridge.logout())
    return {"success": True}


@router.post("/codex/reconnect")
async def reconnect():
    if bridge.active:
        raise HTTPException(409, "Stop running tasks before reconnecting")
    await bridge.close()
    return await bridge.status()


@router.get("/codex/models")
async def models():
    if not await call(bridge.account()):
        return {"models": []}
    return {"models": await call(bridge.models())}


@router.get("/codex/usage")
async def usage():
    if not await call(bridge.account()):
        raise HTTPException(409, "Sign in with ChatGPT to view subscription usage")
    return await call(bridge.request("account/rateLimits/read"))


@router.post("/codex/approve")
async def approve(req: DecisionRequest):
    await call(bridge.decide(req.conversation_id, req.action_id, req.approved))
    return {"success": True}


@router.post("/codex/stop")
async def stop(req: StopRequest):
    await call(bridge.interrupt(req.conversation_id))
    return {"success": True}


@router.post("/codex/answer")
async def answer(req: AnswerRequest):
    await call(bridge.answer(req.conversation_id, req.action_id, req.answers))
    return {"success": True}


@router.get("/preferences")
async def preferences():
    try:
        return load_preferences().model_dump()
    except (ValidationError, ValueError):
        raise HTTPException(409, "Saved preferences are invalid. Check data/preferences.json")


@router.patch("/preferences")
async def update_preferences(changes: dict):
    try:
        return save_preferences(changes).model_dump()
    except (ValidationError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
