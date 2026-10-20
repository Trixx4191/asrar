"""Mistral adapter using the shared native-tool and SSE implementation."""

import os

from .deepseek import DeepSeekProvider


class MistralProvider(DeepSeekProvider):
    name = "mistral"

    def __init__(self):
        super().__init__()
        self.api_key = os.getenv("MISTRAL_API_KEY", "")
        self.base_url = os.getenv("MISTRAL_API_BASE", "https://api.mistral.ai/v1")
