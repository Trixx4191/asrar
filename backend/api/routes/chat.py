"""
Route: /chat
POST /chat        — full response (uses agent loop, returns when done)
POST /chat/stream — SSE streaming with native tool call support

Stream event format (matches what Chat.jsx already expects):
  data: {"meta": true, "model": "...", "task_type": "...", "reason": "..."}
  data: {"token": "..."}
  data: {"tool_start": "tool_name", "args": {...}}
  data: {"tool_result": "tool_name", "preview": "..."}
  data: {"done": true, "tool_calls": [...]} 
  data: {"error": "..."}
"""

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.agent import run_task, _call_tool, SYSTEM_PROMPT, TOOL_SCHEMAS
from core.agent import _to_anthropic_tools, _to_google_tools
from core.router import route
from core import memory, supervisor
from providers import get_provider
from providers.base import Message

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    history: list[dict] = []  # kept for backward-compat; DB history is used when conversation_id is set
    force_model: str | None = None


class ChatResponse(BaseModel):
    response: str
    model_used: str | None
    task_type: str
    routing_reason: str
    tool_calls: list[dict]
    conversation_id: str


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Non-streaming chat — runs the full agentic loop and returns."""
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


@router.post("/stream")
async def chat_stream(req: ChatRequest):
    """SSE streaming chat with native tool call support."""

    async def generator():
        import httpx

        conversation_id = req.conversation_id or memory.create_conversation(
            title=memory.auto_title(req.message)
        )
        yield f"data: {json.dumps({'conversation_id': conversation_id})}\n\n"

        history = memory.get_messages(conversation_id)
        memory.add_message(conversation_id, "user", req.message)

        # ── Route the request (supervisor-aware: sticky during open clarification) ──
        try:
            decision = supervisor.decide_route(conversation_id, req.message, force_model=req.force_model)
        except Exception as e:
            yield f"data: {json.dumps({'error': f'Routing failed: {e}'})}\n\n"
            return

        # Resolve provider with fallback-chain support
        chosen_model = decision.selected_model
        chosen_model_key = decision.selected_model_key
        provider_name = chosen_model["provider"]
        model_id = chosen_model["id"]

        from core.router import load_registry

        registry = load_registry()
        candidate_keys = [chosen_model_key] + decision.fallback_chain
        messages: list[dict] = [{"role": m.role, "content": m.content} for m in history]
        messages.append({"role": "user", "content": req.message})

        provider = None
        text = ""
        tool_calls: list[dict] = []
        last_error = None

        for model_key in candidate_keys:
            candidate_model = registry.get("models", {}).get(model_key)
            if not candidate_model:
                continue
            try:
                candidate_provider = get_provider(candidate_model.get("provider", ""))
            except Exception:
                continue
            if not candidate_provider.is_available():
                continue

            provider = candidate_provider
            chosen_model = candidate_model
            chosen_model_key = model_key
            provider_name = chosen_model["provider"]
            model_id = chosen_model["id"]

            try:
                if provider_name == "anthropic":
                    anth_messages = _to_anthropic_messages(messages)
                    headers = {
                        "x-api-key": provider.api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    }
                    body = {
                        "model": model_id,
                        "max_tokens": 4096,
                        "system": SYSTEM_PROMPT,
                        "tools": _to_anthropic_tools(TOOL_SCHEMAS),
                        "messages": anth_messages,
                        "stream": True,
                    }
                    url = f"{provider.base_url}/messages"
                    text, tool_calls = await _stream_anthropic(url, headers, body, generator_yield=_sse_token)

                elif provider_name == "google":
                    contents = _to_google_contents(messages, SYSTEM_PROMPT)
                    body = {
                        "contents": contents,
                        "tools": _to_google_tools(TOOL_SCHEMAS),
                        "generationConfig": {"maxOutputTokens": 4096},
                    }
                    url = f"{provider.base_url}/models/{model_id}:streamGenerateContent?key={provider.api_key}&alt=sse"
                    text, tool_calls = await _stream_google(url, body, generator_yield=_sse_token)

                else:
                    all_msgs = [{"role": "system", "content": SYSTEM_PROMPT}] + messages
                    headers = {"Authorization": f"Bearer {provider.api_key}", "Content-Type": "application/json"}
                    if hasattr(provider, "headers_extra"):
                        headers.update(provider.headers_extra)
                    body = {
                        "model": model_id,
                        "messages": all_msgs,
                        "max_tokens": 4096,
                        "tools": TOOL_SCHEMAS,
                        "tool_choice": "auto",
                        "stream": True,
                    }
                    url = f"{provider.base_url}/chat/completions"
                    text, tool_calls = await _stream_openai_compat(url, headers, body, generator_yield=_sse_token)

                break
            except httpx.HTTPStatusError as e:
                status = getattr(getattr(e, "response", None), "status_code", "?")
                try:
                    text_preview = e.response.text[:200]
                except Exception:
                    text_preview = "(unable to read error body)"
                try:
                    headers = dict(e.response.headers or {})
                except Exception:
                    headers = {}
                ra = headers.get("retry-after") or headers.get("Retry-After")
                last_error = f"Provider error {status}: {text_preview}" + (f" (retry-after={ra})" if ra else "")
                continue
            except Exception as e:
                last_error = str(e)
                continue

        if not provider or not text:
            yield f"data: {json.dumps({'error': f'No available provider/model. Check API keys in .env or no provider succeeded. Last error: {last_error}'})}\n\n"
            return

        if chosen_model is None:
            yield f"data: {json.dumps({'error': 'No model could be selected for streaming.'})}\n\n"
            return

        if chosen_model_key != decision.selected_model_key:
            decision.reason += f" Fallbacked to '{chosen_model['display_name']}' after an earlier model failed."
            decision.selected_model = chosen_model
            decision.selected_model_key = chosen_model_key

        yield f"data: {json.dumps({'meta': True, 'model': chosen_model['display_name'], 'provider': chosen_model.get('provider', ''), 'task_type': decision.task_type, 'reason': decision.reason, 'sticky': decision.sticky})}\n\n"

        # ── Build initial message list ───────────────────────────────
        # (messages already includes the user turn)

        all_tool_calls: list[dict] = []
        MAX_ITER = 8

        for iteration in range(MAX_ITER):
            # ── Build provider-specific streaming request ─────────────
            try:
                if provider_name == "anthropic":
                    anth_messages = _to_anthropic_messages(messages)
                    headers = {
                        "x-api-key": provider.api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    }
                    body = {
                        "model": model_id,
                        "max_tokens": 4096,
                        "system": SYSTEM_PROMPT,
                        "tools": _to_anthropic_tools(TOOL_SCHEMAS),
                        "messages": anth_messages,
                        "stream": True,
                    }
                    url = f"{provider.base_url}/messages"
                    text, tool_calls = await _stream_anthropic(url, headers, body, generator_yield=_sse_token)

                elif provider_name == "google":
                    contents = _to_google_contents(messages, SYSTEM_PROMPT)
                    body = {
                        "contents": contents,
                        "tools": _to_google_tools(TOOL_SCHEMAS),
                        "generationConfig": {"maxOutputTokens": 4096},
                    }
                    url = f"{provider.base_url}/models/{model_id}:streamGenerateContent?key={provider.api_key}&alt=sse"
                    text, tool_calls = await _stream_google(url, body, generator_yield=_sse_token)

                else:
                    # OpenAI-compat: Groq, DeepSeek, OpenRouter, Mistral
                    all_msgs = [{"role": "system", "content": SYSTEM_PROMPT}] + messages
                    headers = {"Authorization": f"Bearer {provider.api_key}", "Content-Type": "application/json"}
                    if hasattr(provider, "headers_extra"):
                        headers.update(provider.headers_extra)
                    body = {
                        "model": model_id,
                        "messages": all_msgs,
                        "max_tokens": 4096,
                        "tools": TOOL_SCHEMAS,
                        "tool_choice": "auto",
                        "stream": True,
                    }
                    url = f"{provider.base_url}/chat/completions"
                    text, tool_calls = await _stream_openai_compat(url, headers, body, generator_yield=_sse_token)

            except httpx.HTTPStatusError as e:
                # Avoid ResponseNotRead by not assuming response body is readable
                status = getattr(getattr(e, "response", None), "status_code", "?")
                # Try to get a small preview of the response body, but fall back to headers
                try:
                    text_preview = e.response.text[:200]
                except Exception:
                    text_preview = "(unable to read error body)"
                try:
                    headers = dict(e.response.headers or {})
                except Exception:
                    headers = {}
                ra = headers.get("retry-after") or headers.get("Retry-After")
                hdr_preview = f" headers.retry-after={ra}" if ra else ""
                yield f"data: {json.dumps({'error': f'Provider error {status}: {text_preview}{hdr_preview}', 'headers': headers})}\n\n"
                return
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e), 'trace': traceback.format_exc()[:500]})}\n\n"
                return

            # Stream the text tokens we just collected
            for tok in _chunk_text(text):
                yield f"data: {json.dumps({'token': tok})}\n\n"

            if not tool_calls:
                break

            # ── Execute tool calls ────────────────────────────────────
            # Append assistant turn to history
            if provider_name == "anthropic":
                asst_content = []
                if text:
                    asst_content.append({"type": "text", "text": text})
                for tc in tool_calls:
                    asst_content.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["args"]})
                messages.append({"role": "assistant", "content": asst_content})
            else:
                messages.append({
                    "role": "assistant",
                    "content": text or "",
                    "tool_calls": [
                        {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": json.dumps(tc["args"])}}
                        for tc in tool_calls
                    ],
                })

            for tc in tool_calls:
                yield f"data: {json.dumps({'tool_start': tc['name'], 'args': tc['args']})}\n\n"

                result = await _call_tool(tc["name"], tc["args"], conversation_id=conversation_id)
                all_tool_calls.append({"tool": tc["name"], "args": tc["args"], "result": result[:500]})

                yield f"data: {json.dumps({'tool_result': tc['name'], 'preview': result[:300]})}\n\n"

                if provider_name == "anthropic":
                    messages.append({
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": tc["id"], "content": result}],
                    })
                elif provider_name == "google":
                    messages.append({"role": "tool", "name": tc["name"], "content": result})
                else:
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

        supervisor.after_response(
            conversation_id,
            chosen_model_key,
            chosen_model,
            decision.task_type,
            text,
            all_tool_calls,
        )

        memory.add_message(
            conversation_id,
            "assistant",
            text or "",
            model=chosen_model.get("display_name"),
            task_type=decision.task_type,
            tool_calls=all_tool_calls,
        )

        yield f"data: {json.dumps({'done': True, 'tool_calls': all_tool_calls})}\n\n"

    return StreamingResponse(generator(), media_type="text/event-stream")


# ─────────────────────────────────────────────────────────────
# Per-provider streaming parsers
# Each returns (full_text, tool_calls_list)
# tool_calls: [{"id", "name", "args"}]
# ─────────────────────────────────────────────────────────────

def _sse_token(text: str) -> str:
    """Helper — returns an SSE line for a text token."""
    return f"data: {json.dumps({'token': text})}\n\n"


def _chunk_text(text: str, size: int = 6):
    """Yield text in small chunks to simulate streaming."""
    for i in range(0, len(text), size):
        yield text[i : i + size]


async def _stream_openai_compat(url: str, headers: dict, body: dict, generator_yield=None):
    """Stream from any OpenAI-compatible endpoint. Returns (text, tool_calls)."""
    import httpx

    text = ""
    tool_calls_acc: dict[int, dict] = {}

    async with httpx.AsyncClient(timeout=90) as client:
        async with client.stream("POST", url, headers=headers, json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                delta = chunk.get("choices", [{}])[0].get("delta", {})

                if delta.get("content"):
                    text += delta["content"]

                for tc in delta.get("tool_calls") or []:
                    idx = tc["index"]
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {"id": "", "name": "", "args_str": ""}
                    if tc.get("id"):
                        tool_calls_acc[idx]["id"] = tc["id"]
                    fn = tc.get("function", {})
                    if fn.get("name"):
                        tool_calls_acc[idx]["name"] = fn["name"]
                    if fn.get("arguments"):
                        tool_calls_acc[idx]["args_str"] += fn["arguments"]

    tool_calls = []
    for v in tool_calls_acc.values():
        try:
            args = json.loads(v["args_str"]) if v["args_str"] else {}
        except json.JSONDecodeError:
            args = {}
        tool_calls.append({"id": v["id"], "name": v["name"], "args": args})

    return text, tool_calls


async def _stream_anthropic(url: str, headers: dict, body: dict, generator_yield=None):
    """Stream from Anthropic messages API. Returns (text, tool_calls)."""
    import httpx

    text = ""
    tool_calls = []
    current_tool = None

    async with httpx.AsyncClient(timeout=90) as client:
        async with client.stream("POST", url, headers=headers, json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue

                etype = event.get("type", "")

                if etype == "content_block_start":
                    block = event.get("content_block", {})
                    if block.get("type") == "tool_use":
                        current_tool = {"id": block["id"], "name": block["name"], "args_str": ""}

                elif etype == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text += delta.get("text", "")
                    elif delta.get("type") == "input_json_delta" and current_tool:
                        current_tool["args_str"] += delta.get("partial_json", "")

                elif etype == "content_block_stop":
                    if current_tool:
                        try:
                            args = json.loads(current_tool["args_str"]) if current_tool["args_str"] else {}
                        except json.JSONDecodeError:
                            args = {}
                        tool_calls.append({"id": current_tool["id"], "name": current_tool["name"], "args": args})
                        current_tool = None

    return text, tool_calls


async def _stream_google(url: str, body: dict, generator_yield=None):
    """Stream from Google Gemini SSE endpoint. Returns (text, tool_calls)."""
    import httpx

    text = ""
    tool_calls = []

    async with httpx.AsyncClient(timeout=90) as client:
        async with client.stream("POST", url, json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    chunk = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue

                candidate = chunk.get("candidates", [{}])[0]
                for part in candidate.get("content", {}).get("parts", []):
                    if "text" in part:
                        text += part["text"]
                    elif "functionCall" in part:
                        fc = part["functionCall"]
                        tool_calls.append({
                            "id": fc["name"],
                            "name": fc["name"],
                            "args": fc.get("args", {}),
                        })

    return text, tool_calls


# ─────────────────────────────────────────────────────────────
# Message format converters
# ─────────────────────────────────────────────────────────────

def _to_anthropic_messages(messages: list[dict]) -> list[dict]:
    """Convert internal message list to Anthropic format."""
    out = []
    for m in messages:
        role = m["role"]
        content = m.get("content", "")
        if role == "tool":
            out.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": m.get("tool_call_id", ""), "content": content}],
            })
        elif role == "assistant" and isinstance(content, list):
            out.append({"role": "assistant", "content": content})
        else:
            out.append({"role": role, "content": content})
    return out


def _to_google_contents(messages: list[dict], system: str) -> list[dict]:
    """Convert internal message list to Google Gemini contents format."""
    contents = [
        {"role": "user", "parts": [{"text": f"[System]: {system}"}]},
        {"role": "model", "parts": [{"text": "Understood."}]},
    ]
    for m in messages:
        role = m["role"]
        content = m.get("content", "")
        if role == "tool":
            contents.append({
                "role": "user",
                "parts": [{"functionResponse": {"name": m.get("name", "tool"), "response": {"result": content}}}],
            })
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": content or ""}]})
        else:
            contents.append({"role": "user", "parts": [{"text": content}]})
    return contents

