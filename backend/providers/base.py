"""
Base Provider
All model providers inherit from this. Enforces a consistent interface.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Message:
    role: str   # "user" | "assistant" | "system"
    content: str


@dataclass
class ProviderResponse:
    content: str
    model_id: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    success: bool = True
    error: str | None = None


class BaseProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        model_id: str,
        system: str | None = None,
        max_tokens: int = 2048,
        stream: bool = False,
    ) -> ProviderResponse:
        """Send messages to model, return response."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if API key is set."""
        pass
