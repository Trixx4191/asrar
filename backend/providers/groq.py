"""Groq Provider — Llama & Mixtral (OpenAI-compatible) with streaming"""

from __future__ import annotations

import os
import json
import httpx
from typing import AsyncIterator

from .base import (
    BaseProvider,
    Message,
    ProviderResponse,
    ProviderCapabilities,
)


class GroqProvider(BaseProvider):
    name = "groq"
    capabilities = ProviderCapabilities(
        streaming=True,
        native_tools=True,
        vision=False,
        json_mode=True,
        system_message=True,
        max_context=128000,
        tool_calling_quality=0.85,
        reasoning_quality=0.75,
        speed_score=0.95,  # Groq is extremely fast
    )

    def __init__(self):
        super().__init__()
        self.api_key = os.getenv("GROQ_API_KEY", "")
        self.base_url = "https://api.groq.com/openai/v1"

    def is_available(self) -> bool:
        return bool(self.api_key)

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def complete(
        self,
        messages,
        model_id,
        system=None,
        max_tokens=2048,
        stream=False,
        tools=None,
        temperature=None,
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

        body: dict = {
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
            async with httpx.AsyncClient(timeout=90) as client:
                r = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self.headers(),
                    json=body,
                )
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
                    provider=self.name,
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
                content="", model_id=model_id, provider=self.name, success=False, error=msg
            )
        except Exception as e:
            return ProviderResponse(
                content="", model_id=model_id, provider=self.name, success=False, error=str(e)
            )

    async def stream_complete(
        self,
        messages,
        model_id,
        system=None,
        max_tokens=2048,
        tools=None,
        temperature=None,
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
            all_messages.append(entry)

        body: dict = {
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
            async with httpx.AsyncClient(timeout=90) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=self.headers(),
                    json=body,
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
                    content="", model_id=model_id, provider=self.name, success=False, error=str(e)
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
                provider=self.name,
                tool_calls=tool_calls,
            ),
        }
