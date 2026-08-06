"""Google Provider — Gemini with native SSE streaming + function calling"""

from __future__ import annotations

import os
import json
from typing import AsyncIterator

import httpx

from .base import BaseProvider, Message, ProviderResponse, ProviderCapabilities


class GoogleProvider(BaseProvider):
    name = "google"
    capabilities = ProviderCapabilities(
        streaming=True,
        native_tools=True,
        vision=True,
        json_mode=True,
        system_message=True,
        max_context=1000000,
        tool_calling_quality=0.85,
        reasoning_quality=0.8,
        speed_score=0.9,
    )

    def __init__(self):
        super().__init__()
        self.api_key = os.getenv("GOOGLE_API_KEY", "")
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"

    def is_available(self) -> bool:
        return bool(self.api_key)

    def _build_contents(self, messages: list[Message], system: str | None):
        contents = []
        if system:
            contents.append({"role": "user", "parts": [{"text": f"[System]: {system}"}]})
            contents.append({"role": "model", "parts": [{"text": "Understood."}]})
        for m in messages:
            if m.role == "tool":
                contents.append({
                    "role": "user",
                    "parts": [{
                        "functionResponse": {
                            "name": m.name or "tool",
                            "response": {"result": m.content},
                        }
                    }],
                })
            elif m.role == "assistant":
                contents.append({"role": "model", "parts": [{"text": m.content or ""}]})
            else:
                contents.append({"role": "user", "parts": [{"text": m.content or ""}]})
        return contents

    def _to_tools(self, tools: list[dict] | None) -> list[dict] | None:
        if not tools:
            return None
        decls = []
        for t in tools:
            fn = t.get("function", t)
            decls.append({
                "name": fn["name"],
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
            })
        return [{"functionDeclarations": decls}]

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
            "contents": self._build_contents(messages, system),
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        gtools = self._to_tools(tools)
        if gtools:
            body["tools"] = gtools
        if temperature is not None:
            body["generationConfig"]["temperature"] = temperature

        url = f"{self.base_url}/models/{model_id}:generateContent?key={self.api_key}"
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                r = await client.post(url, json=body)
                r.raise_for_status()
                data = r.json()
            text = ""
            tool_calls = []
            for cand in data.get("candidates", []):
                for part in cand.get("content", {}).get("parts", []):
                    if "text" in part:
                        text += part["text"]
                    elif "functionCall" in part:
                        fc = part["functionCall"]
                        tool_calls.append({
                            "id": fc.get("name", "fc"),
                            "name": fc.get("name", ""),
                            "args": fc.get("args") or {},
                        })
            return ProviderResponse(
                content=text, model_id=model_id, provider=self.name, tool_calls=tool_calls
            )
        except httpx.HTTPStatusError as e:
            status = getattr(getattr(e, "response", None), "status_code", "?")
            try:
                preview = e.response.text[:200]
            except Exception:
                preview = "(unable to read)"
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
            "contents": self._build_contents(messages, system),
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        gtools = self._to_tools(tools)
        if gtools:
            body["tools"] = gtools
        if temperature is not None:
            body["generationConfig"]["temperature"] = temperature

        url = (
            f"{self.base_url}/models/{model_id}:streamGenerateContent"
            f"?key={self.api_key}&alt=sse"
        )
        text = ""
        tool_calls = []
        try:
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
                        for cand in chunk.get("candidates", []):
                            for part in cand.get("content", {}).get("parts", []):
                                if "text" in part:
                                    tok = part["text"]
                                    text += tok
                                    yield {"type": "token", "text": tok}
                                elif "functionCall" in part:
                                    fc = part["functionCall"]
                                    tc = {
                                        "id": fc.get("name", "fc"),
                                        "name": fc.get("name", ""),
                                        "args": fc.get("args") or {},
                                    }
                                    tool_calls.append(tc)
                                    yield {
                                        "type": "tool_call_delta",
                                        "index": len(tool_calls) - 1,
                                        "id": tc["id"],
                                        "name": tc["name"],
                                        "arguments": json.dumps(tc["args"]),
                                    }
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
                content=text, model_id=model_id, provider=self.name, tool_calls=tool_calls
            ),
        }
