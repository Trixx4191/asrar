"""Anthropic Provider — Claude models with native streaming + tools"""

from __future__ import annotations

import os
import json
from typing import AsyncIterator

import httpx

from .base import BaseProvider, Message, ProviderResponse, ProviderCapabilities


class AnthropicProvider(BaseProvider):
    name = "anthropic"
    capabilities = ProviderCapabilities(
        streaming=True,
        native_tools=True,
        vision=True,
        json_mode=False,
        system_message=True,
        max_context=200000,
        tool_calling_quality=0.95,
        reasoning_quality=0.9,
        speed_score=0.7,
    )

    def __init__(self):
        super().__init__()
        self.api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.base_url = "https://api.anthropic.com/v1"

    def is_available(self) -> bool:
        return bool(self.api_key)

    def headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def _to_messages(self, messages: list[Message]) -> list[dict]:
        out = []
        for m in messages:
            if m.role == "tool":
                out.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.tool_call_id or "",
                        "content": m.content,
                    }],
                })
            elif m.tool_calls:
                content = []
                if m.content:
                    content.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    content.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": tc.get("function", {}).get("name") or tc.get("name", ""),
                        "input": tc.get("function", {}).get("arguments")
                        if isinstance(tc.get("function", {}).get("arguments"), dict)
                        else json.loads(tc.get("function", {}).get("arguments") or "{}")
                        if isinstance(tc.get("function", {}).get("arguments"), str)
                        else tc.get("args", {}),
                    })
                out.append({"role": "assistant", "content": content})
            else:
                out.append({"role": m.role, "content": m.content})
        return out

    def _to_tools(self, tools: list[dict] | None) -> list[dict] | None:
        if not tools:
            return None
        out = []
        for t in tools:
            fn = t.get("function", t)
            out.append({
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            })
        return out

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
        body: dict = {
            "model": model_id,
            "max_tokens": max_tokens,
            "messages": self._to_messages(messages),
        }
        if system:
            body["system"] = system
        anth_tools = self._to_tools(tools)
        if anth_tools:
            body["tools"] = anth_tools
        if temperature is not None:
            body["temperature"] = temperature

        try:
            async with httpx.AsyncClient(timeout=90) as client:
                r = await client.post(
                    f"{self.base_url}/messages",
                    headers=self.headers(),
                    json=body,
                )
                r.raise_for_status()
                data = r.json()
            text = ""
            tool_calls = []
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text += block.get("text", "")
                elif block.get("type") == "tool_use":
                    tool_calls.append({
                        "id": block["id"],
                        "name": block["name"],
                        "args": block.get("input") or {},
                    })
            usage = data.get("usage") or {}
            return ProviderResponse(
                content=text,
                model_id=model_id,
                provider=self.name,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                tool_calls=tool_calls,
                finish_reason=data.get("stop_reason"),
            )
        except httpx.HTTPStatusError as e:
            status = getattr(getattr(e, "response", None), "status_code", "?")
            try:
                preview = e.response.text[:200]
            except Exception:
                preview = "(unable to read error body)"
            return ProviderResponse(
                content="", model_id=model_id, provider=self.name,
                success=False, error=f"Provider HTTP {status}: {preview}",
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
        body: dict = {
            "model": model_id,
            "max_tokens": max_tokens,
            "messages": self._to_messages(messages),
            "stream": True,
        }
        if system:
            body["system"] = system
        anth_tools = self._to_tools(tools)
        if anth_tools:
            body["tools"] = anth_tools
        if temperature is not None:
            body["temperature"] = temperature

        text = ""
        tool_calls = []
        current_tool = None
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/messages",
                    headers=self.headers(),
                    json=body,
                ) as resp:
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
                                current_tool = {
                                    "id": block["id"],
                                    "name": block["name"],
                                    "args_str": "",
                                }
                        elif etype == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta":
                                tok = delta.get("text", "")
                                text += tok
                                if tok:
                                    yield {"type": "token", "text": tok}
                            elif delta.get("type") == "input_json_delta" and current_tool:
                                current_tool["args_str"] += delta.get("partial_json", "")
                        elif etype == "content_block_stop":
                            if current_tool:
                                try:
                                    args = json.loads(current_tool["args_str"]) if current_tool["args_str"] else {}
                                except json.JSONDecodeError:
                                    args = {}
                                tool_calls.append({
                                    "id": current_tool["id"],
                                    "name": current_tool["name"],
                                    "args": args,
                                })
                                yield {
                                    "type": "tool_call_delta",
                                    "index": len(tool_calls) - 1,
                                    "id": current_tool["id"],
                                    "name": current_tool["name"],
                                    "arguments": current_tool["args_str"],
                                }
                                current_tool = None
        except Exception as e:
            yield {
                "type": "done",
                "response": ProviderResponse(
                    content="", model_id=model_id, provider=self.name,
                    success=False, error=str(e),
                ),
            }
            return

        yield {
            "type": "done",
            "response": ProviderResponse(
                content=text,
                model_id=model_id,
                provider=self.name,
                tool_calls=tool_calls,
            ),
        }
