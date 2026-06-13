"""
Route: /chat
POST /chat        — full response
POST /chat/stream — real per-token SSE streaming
"""

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from core.agent import run_task
from core.router import route
from core.classifier import classify_task
from providers import get_provider
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
    """Stream response with tool execution."""

    async def generator():
        from core.agent import SYSTEM_PROMPT, _build_tool_context, _execute_embedded_tools
        
        history = [Message(role=m["role"], content=m["content"]) for m in req.history]
        decision = route(req.message, force_model=req.force_model)
        provider = get_provider(decision.selected_model["provider"])

        # Send routing metadata first
        yield f"data: {json.dumps({'meta': True, 'model': decision.selected_model['display_name'], 'task_type': decision.task_type, 'reason': decision.reason})}\n\n"

        full_text = ""
        try:
            # Build messages
            tool_ctx = _build_tool_context(decision.task_type, req.message)
            messages = history + [Message(role="user", content=req.message + tool_ctx)]

            # Build stream args for each provider type
            provider_name = decision.selected_model["provider"]
            model_id = decision.selected_model["id"]

            headers, body = _build_stream_payload(provider, provider_name, model_id, messages, SYSTEM_PROMPT)

            # Collect full response as we stream
            async for token in provider._stream(headers, body, model_id):
                full_text += token
                yield f"data: {json.dumps({'token': token})}\n\n"

            # Now execute any embedded tool calls in the full response
            tool_calls = await _execute_embedded_tools(full_text)
            
            if tool_calls:
                tool_summary = "\n".join(f"[{t['tool']}]: {t['result']}" for t in tool_calls)
                yield f"data: {json.dumps({'tools_executed': tool_summary})}\n\n"

        except Exception as e:
            import traceback
            yield f"data: {json.dumps({'error': str(e), 'trace': traceback.format_exc()})}\n\n"

        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(generator(), media_type="text/event-stream")


def _build_stream_payload(provider, provider_name: str, model_id: str, messages, system: str):
    """Build (headers, body) for streaming per provider type."""
    from providers.anthropic import AnthropicProvider
    from providers.google import GoogleProvider

    if isinstance(provider, AnthropicProvider):
        headers = {
            "x-api-key": provider.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {
            "model": model_id,
            "max_tokens": 2048,
            "stream": True,
            "system": system,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        return headers, body

    elif isinstance(provider, GoogleProvider):
        contents = provider._build_contents(messages, system)
        headers = {}  # Google uses URL key, no extra headers
        body = {"contents": contents, "generationConfig": {"maxOutputTokens": 2048}}
        return headers, body

    else:
        # OpenAI-compatible (Groq, DeepSeek, OpenRouter, Mistral)
        all_msgs = [{"role": "system", "content": system}]
        all_msgs += [{"role": m.role, "content": m.content} for m in messages]
        headers = {"Authorization": f"Bearer {provider.api_key}", "Content-Type": "application/json"}
        if hasattr(provider, "headers_extra"):
            headers.update(provider.headers_extra)
        body = {"model": model_id, "messages": all_msgs, "max_tokens": 2048, "stream": True}
        return headers, body
