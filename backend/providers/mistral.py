"""Mistral adapter using the shared native-tool and SSE implementation."""

import os

from .deepseek import DeepSeekProvider
from .base import ProviderCapabilities


class MistralProvider(DeepSeekProvider):
    name = "mistral"
    capabilities = ProviderCapabilities(streaming=True, native_tools=True, json_mode=True)

    def __init__(self):
        super().__init__()
        self.api_key = os.getenv("MISTRAL_API_KEY", "")
        self.base_url = os.getenv("MISTRAL_API_BASE", "https://api.mistral.ai/v1")
