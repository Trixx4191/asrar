"""Providers package initializer.
Provides a factory honoring current credentials and per-model connections.
"""

from .base import BaseProvider



def _load_provider_map():
    # Import providers lazily to avoid import overhead when not needed
    provs = {}
    from .mistral import MistralProvider
    provs[MistralProvider.name] = MistralProvider
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
    try:
        from .qwen import QwenProvider
        provs[QwenProvider.name] = QwenProvider
    except Exception:
        pass
    try:
        from .kimi import KimiProvider
        provs[KimiProvider.name] = KimiProvider
    except Exception:
        pass
    return provs


_fingerprints = {}


def get_provider(name: str, *, model: dict | None = None) -> BaseProvider:
    """Create a connection from current credentials and per-model settings.

    Provider construction is cheap. Avoid caching instances holding stale keys
    or accidentally sharing one endpoint across different model deployments.
    """
    import hashlib
    from .configuration import connection_config, setting
    from .compatible import CompatibleProvider
    name = (name or "").strip().lower()
    config = connection_config(name, model)
    protocol = config["protocol"]
    if protocol == "anthropic":
        from .anthropic import AnthropicProvider
        cls = AnthropicProvider
    elif protocol == "google":
        from .google import GoogleProvider
        cls = GoogleProvider
    else:
        from .configuration import PROVIDER_DEFAULTS
        cls = (_load_provider_map().get(name, CompatibleProvider)
               if PROVIDER_DEFAULTS.get(name, {}).get("protocol") == protocol and not config["local"]
               else CompatibleProvider)
    instance = cls()
    instance.name = name
    instance.base_url = config["api_base"]
    instance.api_key = setting(config["api_key_env"]).strip()
    instance.local = config["local"]
    instance.api_key_env = config["api_key_env"]
    identity = (name, instance.base_url, instance.api_key_env)
    fingerprint = hashlib.sha256(instance.api_key.encode()).digest()
    previous = _fingerprints.get(identity)
    if previous is not None and previous != fingerprint:
        from core.orchestrator import reset_circuit
        reset_circuit(name)
    _fingerprints[identity] = fingerprint
    return instance


__all__ = ["get_provider"]
