"""Provider connection metadata and reloadable local credential settings."""

import os
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"

PROVIDER_DEFAULTS = {
    "anthropic": {"api_base": "https://api.anthropic.com/v1", "env_key": "ANTHROPIC_API_KEY", "protocol": "anthropic"},
    "google": {"api_base": "https://generativelanguage.googleapis.com/v1beta", "env_key": "GOOGLE_API_KEY", "protocol": "google"},
    "groq": {"api_base": "https://api.groq.com/openai/v1", "env_key": "GROQ_API_KEY", "protocol": "openai_compatible"},
    "deepseek": {"api_base": "https://api.deepseek.com/v1", "env_key": "DEEPSEEK_API_KEY", "protocol": "openai_compatible"},
    "mistral": {"api_base": "https://api.mistral.ai/v1", "env_key": "MISTRAL_API_KEY", "protocol": "openai_compatible"},
    "openrouter": {"api_base": "https://openrouter.ai/api/v1", "env_key": "OPENROUTER_API_KEY", "protocol": "openai_compatible"},
    "qwen": {"api_base": "https://api.qwen.ai", "env_key": "QWEN_API_KEY", "protocol": "openai_compatible"},
    "kimi": {"api_base": "https://api.kimi.ai", "env_key": "KIMI_API_KEY", "protocol": "openai_compatible"},
    "together": {"api_base": "https://api.together.xyz/v1", "env_key": "TOGETHER_API_KEY", "protocol": "openai_compatible"},
    "cohere": {"api_base": "https://api.cohere.ai/compatibility/v1", "env_key": "COHERE_API_KEY", "protocol": "openai_compatible"},
    "xai": {"api_base": "https://api.x.ai/v1", "env_key": "XAI_API_KEY", "protocol": "openai_compatible"},
    "openai": {"api_base": "https://api.openai.com/v1", "env_key": "OPENAI_API_KEY", "protocol": "openai_compatible"},
}


@lru_cache(maxsize=1)
def _file_settings(path: str, modified: int, size: int) -> dict:
    return dotenv_values(path, interpolate=False)


def setting(name: str, default: str = "") -> str:
    """The local file is authoritative, including an intentionally empty value.

    Never copy file secrets into process-global environment variables. A fresh
    provider reads the latest file settings without retaining an old API key.
    """
    try:
        stat = ENV_PATH.stat()
        values = _file_settings(str(ENV_PATH), stat.st_mtime_ns, stat.st_size)
    except FileNotFoundError:
        values = {}
    return str(values[name] or "") if name in values else os.getenv(name, default)


def redact_secrets(text: str) -> str:
    values = dict(os.environ)
    try:
        stat = ENV_PATH.stat()
        values.update(_file_settings(str(ENV_PATH), stat.st_mtime_ns, stat.st_size))
    except FileNotFoundError:
        pass
    for name, value in values.items():
        if value and (name.endswith("_API_KEY") or name.endswith("_TOKEN")):
            text = text.replace(str(value), "[redacted]")
    return text


def connection_config(provider: str, model: dict | None = None) -> dict:
    provider = provider.strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", provider):
        raise ValueError("Provider must be a simple lowercase identifier")
    model = model or {}
    defaults = PROVIDER_DEFAULTS.get(provider, {})
    protocol = model.get("protocol") or defaults.get("protocol")
    if protocol not in {"openai_compatible", "anthropic", "google"}:
        raise ValueError("Unknown provider protocol. Supply openai_compatible, anthropic, or google and its API base URL")
    env_key = model.get("api_key_env") or defaults.get("env_key") or f"{provider.upper().replace('-', '_')}_API_KEY"
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", env_key):
        raise ValueError("api_key_env must be an environment variable name, never an API key value")
    # Environment overrides allow a local proxy without losing persisted
    # per-model endpoints when the provider has several deployments.
    base_env = f"{provider.upper().replace('-', '_')}_API_BASE"
    base = setting(base_env) or model.get("api_base") or defaults.get("api_base") or ""
    url = urlsplit(base)
    local = url.hostname in {"localhost", "127.0.0.1", "::1"}
    if (not url.hostname or url.username or url.password or url.query or url.fragment
            or (url.scheme != "https" and not (url.scheme == "http" and local))):
        raise ValueError("API base must be HTTPS (or localhost HTTP), with no credentials, query, or fragment")
    return {"protocol": protocol, "api_base": base.rstrip("/"), "api_key_env": env_key, "local": local}
