"""Groq adapter, using the shared OpenAI-compatible tool/stream transport."""
import os
from .deepseek import DeepSeekProvider
from .base import ProviderCapabilities

class GroqProvider(DeepSeekProvider):
    name = "groq"
    capabilities = ProviderCapabilities(streaming=True, native_tools=True,
        json_mode=True, max_context=128000, speed_score=0.95)

    def __init__(self):
        super().__init__()
        self.api_key = os.getenv("GROQ_API_KEY", "")
        self.base_url = os.getenv("GROQ_API_BASE", "https://api.groq.com/openai/v1")
