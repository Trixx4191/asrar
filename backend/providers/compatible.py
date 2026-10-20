"""Configurable adapter for providers implementing the chat-completions protocol."""

from .deepseek import DeepSeekProvider
from .base import ProviderCapabilities


class CompatibleProvider(DeepSeekProvider):
    capabilities = ProviderCapabilities(streaming=True, native_tools=True)

    def is_available(self):
        return bool(self.api_key) or getattr(self, "local", False)

    def headers(self):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers
