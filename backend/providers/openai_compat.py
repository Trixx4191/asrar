
"""Shared OpenAI-compatible streaming/completion helpers for Groq, DeepSeek, OpenRouter, etc."""

from __future__ import annotations

import json
from typing import AsyncIterator, Any

import httpx

from .base import Message, ProviderResponse


async def openai_complete(
    *,
    base_url: str,
    headers: dict,
    model_id: str,
    messages: list[Message],
    system: str | None,
    max_tokens: int,
    tools: list[dict] | None,
    temperature: float | None,
    provider_name: str,
    timeout: float = 90,
) -> ProviderResponse:
    all_messages = []
    if system:
        all_messages.append({"role": "system", "content": system})
    for m in messages:
        entry: dict = {"role": m.role, "content": m.content}
        if m.tool_calls:
            entry["tool_calls"] = m.tool_calls
        if m.tool_call_id:
            entry["tool_call_id"] = m.tool_call_id
        if m.name:
            entry["name"] = m.name
        all_messages.append(entry)

    body: dict[str, Any] = {
        "model": model_id,
        "messages": all_messages,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if temperature is not None:
        body["temperature"] = temperature

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{base_url}/chat/completions", headers=headers, json=body)
            r.raise_for_status()
            data = r.json()
            choice = data["choices"][0]
            msg = choice.get("message", {})
            tool_calls = []
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append({
                    "id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "args": args,
                })
            return ProviderResponse(
                content=msg.get("content") or "",
                model_id=model_id,
                provider=provider_name,
                input_tokens=data.get("usage", {}).get("prompt_tokens", 0),
                output_tokens=data.get("usage", {}).get("completion_tokens", 0),
                tool_calls=tool_calls,
                finish_reason=choice.get("finish_reason"),
            )
    except httpx.HTTPStatusError as e:
        status = getattr(getattr(e, "response", None), "status_code", "?")
        try:
            preview = e.response.text[:200]
        except Exception:
            preview = "(unable to read error body)"
        try:
            hdrs = dict(e.response.headers or {})
        except Exception:
            hdrs = {}
        ra = hdrs.get("retry-after") or hdrs.get("Retry-After")
        msg = f"Provider HTTP {status}: {preview}" + (f" (retry-after={ra})" if ra else "")
        return ProviderResponse(
            content="", model_id=model_id, provider=provider_name, success=False, error=msg
        )
    except Exception as e:
        return ProviderResponse(
            content="", model_id=model_id, provider=provider_name, success=False, error=str(e)
        )


async def openai_stream(
    *,
    base_url: str,
    headers: dict,
    model_id: str,
    messages: list[Message],
    system: str | None,
    max_tokens: int,
    tools: list[dict] | None,
    temperature: float | None,
    provider_name: str,
    timeout: float = 90,
) -> AsyncIterator[dict]:
    all_messages = []
    if system:
        all_messages.append({"role": "system", "content": system})
    for m in messages:
        entry: dict = {"role": m.role, "content": m.content}
        if m.tool_calls:
            entry["tool_calls"] = m.tool_calls
        if m.tool_call_id:
            entry["tool_call_id"] = m.tool_call_id
        if m.name:
            entry["name"] = m.name
        all_messages.append(entry)

    body: dict[str, Any] = {
        "model": model_id,
        "messages": all_messages,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if temperature is not None:
        body["temperature"] = temperature

    text = ""
    tool_calls_acc: dict[int, dict] = {}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST", f"{base_url}/chat/completions", headers=headers, json=body
            ) as resp:
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
                        yield {"type": "token", "text": delta["content"]}
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
                        yield {
                            "type": "tool_call_delta",
                            "index": idx,
                            "id": tool_calls_acc[idx]["id"],
                            "name": tool_calls_acc[idx]["name"],
                            "arguments": tool_calls_acc[idx]["args_str"],
                        }
    except Exception as e:
        yield {
            "type": "done",
            "response": ProviderResponse(
                content="", model_id=model_id, provider=provider_name, success=False, error=str(e)
            ),
        }
        return

    tool_calls = []
    for v in tool_calls_acc.values():
        try:
            args = json.loads(v["args_str"]) if v["args_str"] else {}
        except json.JSONDecodeError:
            args = {}
        tool_calls.append({"id": v["id"], "name": v["name"], "args": args})

    yield {
        "type": "done",
        "response": ProviderResponse(
            content=text,
            model_id=model_id,
            provider=provider_name,
            tool_calls=tool_calls,
        ),
    }
