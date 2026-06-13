"""Providers package initializer.
Provides a `get_provider(name)` factory and caches provider instances.
"""
from typing import Dict

from .base import BaseProvider

_INSTANCES: Dict[str, BaseProvider] = {}


def _load_provider_map():
    # Import providers lazily to avoid import overhead when not needed
    provs = {}
    try:
        from .openrouter import OpenRouterProvider
        provs[OpenRouterProvider.name] = OpenRouterProvider
    except Exception:
        pass
    try:
        from .google import GoogleProvider
        provs[GoogleProvider.name] = GoogleProvider
    except Exception:
        pass
    try:
        from .groq import GroqProvider
        provs[GroqProvider.name] = GroqProvider
    except Exception:
        pass
    try:
        from .deepseek import DeepSeekProvider
        provs[DeepSeekProvider.name] = DeepSeekProvider
    except Exception:
        pass
    try:
        from .anthropic import AnthropicProvider
        provs[AnthropicProvider.name] = AnthropicProvider
    except Exception:
        pass
    return provs


def get_provider(name: str) -> BaseProvider:
    """Return a provider instance by name. Returns a BaseProvider with the
    configured API key state; callers should call `is_available()` before use.
    """
    name = (name or "").lower()
    if name in _INSTANCES:
        return _INSTANCES[name]

    prov_map = _load_provider_map()
    cls = prov_map.get(name)
    if cls is None:
        raise ValueError(f"Unknown provider: {name}")

    inst = cls()
    _INSTANCES[name] = inst
    return inst


__all__ = ["get_provider"]
