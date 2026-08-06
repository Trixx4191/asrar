"""Kimi Provider (OpenAI-compatible)"""

from __future__ import annotations
import os
from typing import AsyncIterator
from .base import BaseProvider, Message, ProviderResponse, ProviderCapabilities
from .openai_compat import openai_complete, openai_stream

class KimiProvider(BaseProvider):
    name = "kimi"
    capabilities = ProviderCapabilities(
        streaming=True, native_tools=True, vision=False, json_mode=True,
        system_message=True, max_context=128000,
        tool_calling_quality=0.8, reasoning_quality=0.85, speed_score=0.75,
    )
    def __init__(self):
        super().__init__()
        self.api_key = os.getenv("KIMI_API_KEY", "")
        self.base_url = os.getenv("KIMI_API_BASE", "https://api.kimi.ai")
    def is_available(self) -> bool:
        return bool(self.api_key)
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
    async def complete(self, messages, model_id, system=None, max_tokens=2048, stream=False, tools=None, temperature=None) -> ProviderResponse:
        return await openai_complete(
            base_url=self.base_url, headers=self.headers(), model_id=model_id,
            messages=messages, system=system, max_tokens=max_tokens, tools=tools,
            temperature=temperature, provider_name=self.name,
        )
    async def stream_complete(self, messages, model_id, system=None, max_tokens=2048, tools=None, temperature=None) -> AsyncIterator[dict]:
        async for ev in openai_stream(
            base_url=self.base_url, headers=self.headers(), model_id=model_id,
            messages=messages, system=system, max_tokens=max_tokens, tools=tools,
            temperature=temperature, provider_name=self.name,
        ):
            yield ev
