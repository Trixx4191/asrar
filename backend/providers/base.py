"""
Unified Provider Adapter
All model providers inherit from BaseProvider.
Capability detection lets the orchestrator/router know what each
provider actually supports (streaming, native tools, vision, etc.).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class Message:
    role: str  # "user" | "assistant" | "system" | "tool"
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict] | None = None


@dataclass
class ProviderResponse:
    content: str
    model_id: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    success: bool = True
    error: str | None = None
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str | None = None


@dataclass
class ProviderCapabilities:
    """Declared capabilities — used for routing and feature gating."""
    streaming: bool = True
    native_tools: bool = True
    vision: bool = False
    json_mode: bool = False
    system_message: bool = True
    max_context: int = 32000
    # Quality hints (0.0–1.0) for the router
    tool_calling_quality: float = 0.7
    reasoning_quality: float = 0.7
    speed_score: float = 0.7  # higher = faster


class BaseProvider(ABC):
    name: str = "base"
    capabilities: ProviderCapabilities = ProviderCapabilities()

    def __init__(self):
        # Subclasses should set self.api_key, self.base_url, etc.
        self._capabilities_detected = False

    @abstractmethod
    def is_available(self) -> bool:
        """True when an API key is configured."""
        ...

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        model_id: str,
        system: str | None = None,
        max_tokens: int = 2048,
        stream: bool = False,
        tools: list[dict] | None = None,
        temperature: float | None = None,
    ) -> ProviderResponse:
        """Non-streaming (or fully-buffered) completion."""
        ...

    async def stream_complete(
        self,
        messages: list[Message],
        model_id: str,
        system: str | None = None,
        max_tokens: int = 2048,
        tools: list[dict] | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[dict]:
        """
        Yield events:
          {"type": "token", "text": "..."}
          {"type": "tool_call_delta", "index": 0, "id": "...", "name": "...", "arguments": "..."}
          {"type": "done", "response": ProviderResponse}
        Default implementation falls back to complete() and yields one token block.
        Override for true streaming.
        """
        resp = await self.complete(
            messages=messages,
            model_id=model_id,
            system=system,
            max_tokens=max_tokens,
            stream=False,
            tools=tools,
            temperature=temperature,
        )
        if resp.content:
            yield {"type": "token", "text": resp.content}
        yield {"type": "done", "response": resp}

    def get_capabilities(self) -> ProviderCapabilities:
        return self.capabilities

    def supports(self, feature: str) -> bool:
        caps = self.get_capabilities()
        return bool(getattr(caps, feature, False))

    async def detect_capabilities(self, model_id: str | None = None) -> ProviderCapabilities:
        """
        Optional live probe. Subclasses may override to query the provider
        (e.g. list models endpoint) and refine capabilities at runtime.
        Default: return the static declaration.
        """
        self._capabilities_detected = True
        return self.capabilities

    def headers(self) -> dict[str, str]:
        """Common auth headers. Override when needed."""
        return {}
