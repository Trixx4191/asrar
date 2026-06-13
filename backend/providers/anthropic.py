"""Anthropic Provider — Claude models"""

import os
import httpx
from .base import BaseProvider, Message, ProviderResponse


class AnthropicProvider(BaseProvider):
    name = "anthropic"

    def __init__(self):
        self.api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.base_url = "https://api.anthropic.com/v1"

    def is_available(self) -> bool:
        return bool(self.api_key)

    async def complete(self, messages, model_id, system=None, max_tokens=2048, stream=False) -> ProviderResponse:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        body = {
            "model": model_id,
            "max_tokens": max_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if system:
            body["system"] = system

        try:
            if stream:
                return self._stream(headers, body, model_id)
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(f"{self.base_url}/messages", headers=headers, json=body)
                r.raise_for_status()
                data = r.json()
                return ProviderResponse(
                    content=data["content"][0]["text"],
                    model_id=model_id,
                    provider=self.name,
                    input_tokens=data.get("usage", {}).get("input_tokens", 0),
                    output_tokens=data.get("usage", {}).get("output_tokens", 0),
                )
        except Exception as e:
            return ProviderResponse(content="", model_id=model_id, provider=self.name, success=False, error=str(e))

    async def _stream(self, headers, body, model_id):
        body["stream"] = True
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream("POST", f"{self.base_url}/messages", headers=headers, json=body) as r:
                async for line in r.aiter_lines():
                    if line.startswith("data:"):
                        data = line[5:].strip()
                        if data == "[DONE]": break
                        try:
                            import json
                            chunk = json.loads(data)
                            if chunk.get("type") == "content_block_delta":
                                yield chunk["delta"].get("text", "")
                        except Exception:
                            pass
