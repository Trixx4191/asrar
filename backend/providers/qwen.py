"""Qwen Provider — basic OpenAI-compatible wrapper for Qwen endpoints
"""

import os
import json
import httpx
from .base import BaseProvider, Message, ProviderResponse


class QwenProvider(BaseProvider):
    name = "qwen"

    def __init__(self):
        self.api_key = os.getenv("QWEN_API_KEY", "")
        self.base_url = os.getenv("QWEN_API_BASE", "https://api.qwen.ai")

    def is_available(self) -> bool:
        return bool(self.api_key)

    async def complete(self, messages, model_id, system=None, max_tokens=2048, stream=False) -> ProviderResponse:
        all_messages = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages += [{"role": m.role, "content": m.content} for m in messages]

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        body = {"model": model_id, "messages": all_messages, "max_tokens": max_tokens, "stream": stream}

        try:
            if stream:
                return self._stream(headers, body, model_id)

            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=body)
                r.raise_for_status()
                data = r.json()
                return ProviderResponse(
                    content=data["choices"][0]["message"]["content"],
                    model_id=model_id,
                    provider=self.name,
                    input_tokens=data.get("usage", {}).get("prompt_tokens", 0),
                    output_tokens=data.get("usage", {}).get("completion_tokens", 0),
                )
        except httpx.HTTPStatusError as e:
            status = getattr(getattr(e, "response", None), "status_code", "?")
            try:
                preview = e.response.text[:200]
            except Exception:
                preview = "(unable to read error body)"
            try:
                headers = dict(e.response.headers or {})
            except Exception:
                headers = {}
            ra = headers.get("retry-after") or headers.get("Retry-After")
            msg = f"Provider HTTP {status}: {preview}" + (f" (retry-after={ra})" if ra else "")
            return ProviderResponse(content="", model_id=model_id, provider=self.name, success=False, error=msg)
        except Exception as e:
            return ProviderResponse(content="", model_id=model_id, provider=self.name, success=False, error=str(e))

    async def _stream(self, headers, body, model_id):
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream("POST", f"{self.base_url}/chat/completions", headers=headers, json=body) as r:
                async for line in r.aiter_lines():
                    if line.startswith("data:"):
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk["choices"][0]["delta"].get("content", "")
                            if delta:
                                yield delta
                        except Exception:
                            pass
