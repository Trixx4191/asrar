"""
Model Registry Manager
Handles adding new models (manually or via agent lookup),
updating existing ones, and self-registering from a URL/name.
"""

import json
import os
import httpx
from pathlib import Path
from datetime import datetime


CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "models.json"

PROVIDER_DEFAULTS = {
    "openrouter": {
        "api_base": "https://openrouter.ai/api/v1",
        "env_key": "OPENROUTER_API_KEY"
    },
    "groq": {
        "api_base": "https://api.groq.com/openai/v1",
        "env_key": "GROQ_API_KEY"
    },
    "anthropic": {
        "api_base": "https://api.anthropic.com/v1",
        "env_key": "ANTHROPIC_API_KEY"
    },
    "google": {
        "api_base": "https://generativelanguage.googleapis.com/v1beta",
        "env_key": "GOOGLE_API_KEY"
    },
    "deepseek": {
        "api_base": "https://api.deepseek.com/v1",
        "env_key": "DEEPSEEK_API_KEY"
    },
    "mistral": {
        "api_base": "https://api.mistral.ai/v1",
        "env_key": "MISTRAL_API_KEY"
    },
    "together": {
        "api_base": "https://api.together.xyz/v1",
        "env_key": "TOGETHER_API_KEY"
    },
    "cohere": {
        "api_base": "https://api.cohere.ai/v1",
        "env_key": "COHERE_API_KEY"
    }
}


def load_registry() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def save_registry(registry: dict):
    with open(CONFIG_PATH, "w") as f:
        json.dump(registry, f, indent=2)
    print(f"[Registry] Saved at {datetime.now().strftime('%H:%M:%S')}")


def add_model(
    key: str,
    model_id: str,
    provider: str,
    display_name: str,
    strengths: list[str],
    context_window: int = 32000,
    api_base: str | None = None,
    free: bool = True,
    priority: int = 2,
) -> dict:
    """
    Add a new model to the registry.
    Returns the added model entry.
    """
    registry = load_registry()

    if key in registry["models"]:
        raise ValueError(f"Model key '{key}' already exists. Use update_model() instead.")

    provider_info = PROVIDER_DEFAULTS.get(provider, {})
    resolved_api_base = api_base or provider_info.get("api_base", "")

    entry = {
        "id": model_id,
        "provider": provider,
        "display_name": display_name,
        "api_base": resolved_api_base,
        "strengths": strengths,
        "context_window": context_window,
        "free": free,
        "enabled": True,
        "priority": priority,
        "added_at": datetime.now().isoformat()
    }

    registry["models"][key] = entry
    save_registry(registry)
    print(f"[Registry] ✅ Added model: {display_name} ({key})")
    return entry


def remove_model(key: str) -> bool:
    registry = load_registry()
    if key not in registry["models"]:
        print(f"[Registry] ⚠️  Model '{key}' not found.")
        return False
    name = registry["models"][key]["display_name"]
    del registry["models"][key]
    save_registry(registry)
    print(f"[Registry] 🗑️  Removed model: {name}")
    return True


def toggle_model(key: str, enabled: bool) -> bool:
    registry = load_registry()
    if key not in registry["models"]:
        return False
    registry["models"][key]["enabled"] = enabled
    save_registry(registry)
    status = "enabled" if enabled else "disabled"
    print(f"[Registry] Model '{key}' {status}.")
    return True


def update_task_strengths(key: str, strengths: list[str]):
    """Update which task types a model is good at."""
    registry = load_registry()
    if key not in registry["models"]:
        return False
    registry["models"][key]["strengths"] = strengths
    save_registry(registry)
    return True


def list_models(only_enabled: bool = False) -> list[dict]:
    registry = load_registry()
    models = []
    for key, model in registry["models"].items():
        if only_enabled and not model.get("enabled", True):
            continue
        models.append({"key": key, **model})
    return sorted(models, key=lambda m: m.get("priority", 0), reverse=True)


async def lookup_and_register_model(name_or_url: str) -> dict | None:
    """
    Given a model name or URL, tries to figure out the provider,
    model ID, and capabilities, then registers it automatically.
    This is called by the agent when you tell it to add a new model.
    """
    print(f"[Registry] 🔍 Looking up model: {name_or_url}")

    # Detect provider from URL or name
    provider = _detect_provider(name_or_url)
    model_id = _extract_model_id(name_or_url)
    key = model_id.replace("/", "-").replace(":", "-").lower()

    # Try to fetch model info from OpenRouter (has a great model index)
    capabilities = await _fetch_openrouter_info(model_id)

    if capabilities:
        return add_model(
            key=key,
            model_id=capabilities.get("id", model_id),
            provider=provider or capabilities.get("provider", "openrouter"),
            display_name=capabilities.get("name", model_id),
            strengths=capabilities.get("strengths", ["general"]),
            context_window=capabilities.get("context_length", 32000),
            free=capabilities.get("pricing", {}).get("prompt", "1") == "0",
            priority=2
        )

    # Fallback: register with basic info
    return add_model(
        key=key,
        model_id=model_id,
        provider=provider or "openrouter",
        display_name=name_or_url,
        strengths=["general"],
        priority=2
    )


def _detect_provider(text: str) -> str | None:
    text = text.lower()
    for provider in PROVIDER_DEFAULTS:
        if provider in text:
            return provider
    if "openrouter" in text or "/" in text:
        return "openrouter"
    return None


def _extract_model_id(text: str) -> str:
    # If it's a URL, grab the last path segment
    if text.startswith("http"):
        return text.rstrip("/").split("/")[-1]
    return text.strip()


async def _fetch_openrouter_info(model_id: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get("https://openrouter.ai/api/v1/models")
            if r.status_code == 200:
                models = r.json().get("data", [])
                for m in models:
                    if model_id.lower() in m.get("id", "").lower():
                        # Map OpenRouter fields to our format
                        return {
                            "id": m["id"],
                            "name": m.get("name", model_id),
                            "context_length": m.get("context_length", 32000),
                            "pricing": m.get("pricing", {}),
                            "strengths": _infer_strengths(m.get("name", ""), m.get("description", ""))
                        }
    except Exception as e:
        print(f"[Registry] OpenRouter lookup failed: {e}")
    return None


def _infer_strengths(name: str, description: str) -> list[str]:
    text = (name + " " + description).lower()
    strengths = []
    if any(w in text for w in ["reason", "think", "logic"]):
        strengths.append("deep_reasoning")
    if any(w in text for w in ["code", "coder", "programming"]):
        strengths.append("coding")
    if any(w in text for w in ["fast", "flash", "turbo", "mini"]):
        strengths.append("fast_chat")
    if any(w in text for w in ["vision", "multimodal", "image"]):
        strengths.append("multimodal")
    if not strengths:
        strengths.append("general")
    return strengths


if __name__ == "__main__":
    print("=== Current Models ===")
    for m in list_models():
        status = "✅" if m.get("enabled") else "❌"
        print(f"{status} [{m['key']}] {m['display_name']} — {m['provider']}")
