"""
Route: /chat
Main endpoint — sends user message to agent, returns response.
"""

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from core.agent import run_task
from providers.base import Message

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []
    force_model: str | None = None


class ChatResponse(BaseModel):
    response: str
    model_used: str | None
    task_type: str
    routing_reason: str
    tool_calls: list[dict]


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest):
    history = [Message(role=m["role"], content=m["content"]) for m in req.history]
    result = await run_task(
        user_input=req.message,
        history=history,
        force_model=req.force_model,
    )
    return ChatResponse(**result)


@router.post("/stream")
async def chat_stream(req: ChatRequest):
    """Stream response token by token."""
    async def generator():
        history = [Message(role=m["role"], content=m["content"]) for m in req.history]
        result = await run_task(req.message, history=history, force_model=req.force_model)
        # Stream word by word for now (true streaming per-provider coming in Phase 5)
        words = result["response"].split(" ")
        for i, word in enumerate(words):
            chunk = word + ("" if i == len(words) - 1 else " ")
            yield f"data: {json.dumps({'token': chunk})}\n\n"
        yield f"data: {json.dumps({'done': True, 'model_used': result['model_used'], 'task_type': result['task_type']})}\n\n"

    return StreamingResponse(generator(), media_type="text/event-stream")
