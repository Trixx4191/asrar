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

from providers.configuration import PROVIDER_DEFAULTS, connection_config
from providers import configuration
from providers import get_provider
from providers.base import Message
from core.classifier import TASK_TYPES
from urllib.parse import urlsplit, unquote
from threading import RLock
import asyncio
import re
import tempfile

_REGISTRY_LOCK = RLock()


def load_registry() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def save_registry(registry: dict):
    """Atomic replacement prevents readers observing a half-written registry."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=CONFIG_PATH.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(registry, handle, indent=2)
            handle.write("\n")
            handle.close()
            os.replace(temporary, CONFIG_PATH)
        finally:
            temporary.unlink(missing_ok=True)


def _sync_task_map(registry: dict, key: str, strengths: list[str]):
    task_map = registry.setdefault("task_model_map", {})
    for candidates in task_map.values():
        candidates[:] = [candidate for candidate in candidates if candidate != key]
    for task in strengths:
        candidates = task_map.setdefault(task, [])
        candidates.append(key)
        candidates.sort(key=lambda candidate: -registry["models"].get(candidate, {}).get("priority", 0))


def _ensure_key_placeholder(name: str):
    # Keys are entered locally, never supplied as tool arguments or model context.
    path = configuration.ENV_PATH
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if re.search(r"^\s*(?:export\s+)?" + re.escape(name) + r"\s*=", existing, re.M):
        return
    if os.getenv(name):
        return  # An empty file value must not mask a configured process secret.
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
        handle.write(("\n" if existing and not existing.endswith("\n") else "") + name + "=\n")


def _validated_entry(key: str, entry: dict) -> dict:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", key):
        raise ValueError("Model key must use lowercase letters, digits, dots, underscores, or hyphens")
    model_id = entry.get("id", "")
    if not isinstance(model_id, str) or not model_id.strip() or re.search(r"\s", model_id) or len(model_id) > 256:
        raise ValueError("Use an exact model ID without whitespace; use lookup for display names")
    if model_id.startswith(("sk-", "sk_")):
        raise ValueError("An API key is not a model ID. Enter credentials only in the local .env file")
    provider = entry.get("provider", "").strip().lower()
    entry["provider"] = provider
    strengths = entry.get("strengths") or ["general"]
    if any(task not in TASK_TYPES for task in strengths):
        raise ValueError("Model strengths must use Asrār task types: " + ", ".join(TASK_TYPES))
    entry["strengths"] = list(dict.fromkeys(strengths))
    if not 0 <= entry.get("priority", 2) <= 100 or entry.get("context_window", 32000) < 1:
        raise ValueError("Priority must be 0–100 and context_window must be positive")
    config = connection_config(provider, entry)
    if config["protocol"] == "anthropic" and "/" in model_id:
        raise ValueError("Anthropic native model IDs do not use an organization/ prefix; use openrouter for gateway IDs")
    entry.update({name: config[name] for name in ("protocol", "api_base", "api_key_env")})
    return entry


def add_model(key: str, model_id: str, provider: str, display_name: str,
              strengths: list[str], context_window: int = 32000,
              api_base: str | None = None, free: bool = False, priority: int = 2,
              protocol: str | None = None, api_key_env: str | None = None) -> dict:
    entry = _validated_entry(key, {
        "id": model_id, "provider": provider, "display_name": display_name,
        "strengths": strengths, "context_window": context_window,
        "api_base": api_base, "free": free, "priority": priority,
        "protocol": protocol, "api_key_env": api_key_env, "enabled": True,
        "added_at": datetime.now().isoformat(), "validation": {"status": "untested"},
    })
    with _REGISTRY_LOCK:
        registry = load_registry()
        if key in registry["models"]:
            raise ValueError(f"Model '{key}' already exists. Use configure_model to update it")
        _ensure_key_placeholder(entry["api_key_env"])
        registry["models"][key] = entry
        _sync_task_map(registry, key, entry["strengths"])
        save_registry(registry)
    return {"key": key, **entry}


def configure_model(key: str, **changes) -> dict:
    allowed = {"model_id", "provider", "display_name", "strengths", "context_window", "api_base", "free", "priority", "protocol", "api_key_env"}
    if set(changes) - allowed:
        raise ValueError("Unsupported model configuration fields")
    if not changes:
        raise ValueError("Supply at least one configuration field to update")
    with _REGISTRY_LOCK:
        registry = load_registry()
        if key not in registry["models"]:
            raise ValueError(f"Unknown model '{key}'")
        original = registry["models"][key]
        entry = dict(original)
        if "provider" in changes and changes["provider"] != entry["provider"]:
            # A provider switch must not retain another service's key or endpoint.
            for field in ("protocol", "api_base", "api_key_env"):
                entry.pop(field, None)
        if "model_id" in changes:
            changes["id"] = changes.pop("model_id")
        if "strengths" not in changes:
            # Older registry entries mixed routing tasks with descriptive tags.
            # Preserve that metadata while allowing their connections to be repaired.
            aliases = {"chat": "fast_chat", "lightweight": "fast_chat", "fast_reasoning": "deep_reasoning",
                       "current_events": "web_research", "math": "deep_reasoning", "logic": "deep_reasoning",
                       "complex_planning": "deep_reasoning", "debugging": "coding", "shell_scripts": "shell_automation",
                       "simple_tasks": "fast_chat", "quick_answers": "fast_chat"}
            old = entry.get("strengths", [])
            tasks = list(dict.fromkeys(aliases.get(task, task) for task in old if aliases.get(task, task) in TASK_TYPES))
            if tasks != old:
                entry["capability_tags"] = list(dict.fromkeys(entry.get("capability_tags", []) + old))
                entry["strengths"] = tasks or ["general"]
        entry.update(changes)
        entry = _validated_entry(key, entry)
        entry["validation"] = {"status": "untested"}
        _ensure_key_placeholder(entry["api_key_env"])
        registry["models"][key] = entry
        _sync_task_map(registry, key, entry["strengths"])
        save_registry(registry)
    return {"key": key, **entry}


def remove_model(key: str) -> bool:
    with _REGISTRY_LOCK:
        registry = load_registry()
        if key not in registry["models"]:
            return False
        del registry["models"][key]
        _sync_task_map(registry, key, [])
        save_registry(registry)
    return True


def toggle_model(key: str, enabled: bool) -> bool:
    with _REGISTRY_LOCK:
        registry = load_registry()
        if key not in registry["models"]:
            return False
        registry["models"][key]["enabled"] = enabled
        save_registry(registry)
    return True


def update_task_strengths(key: str, strengths: list[str]):
    try:
        configure_model(key, strengths=strengths)
        return True
    except ValueError:
        return False


def list_models(only_enabled: bool = False) -> list[dict]:
    from providers.configuration import setting
    registry = load_registry()
    models = []
    for key, model in registry["models"].items():
        if only_enabled and not model.get("enabled", True):
            continue
        try:
            config = connection_config(model["provider"], model)
            readiness = {
                "configured": config["local"] or bool(setting(config["api_key_env"]).strip()),
                "api_key_env": config["api_key_env"], "protocol": config["protocol"],
            }
        except ValueError as exc:
            readiness = {"configured": False, "configuration_error": str(exc)}
        item = {"key": key, **model, **readiness}
        validation = model.get("validation") or {}
        if validation.get("tested_at"):
            try:
                if configuration.ENV_PATH.stat().st_mtime > datetime.fromisoformat(validation["tested_at"]).timestamp():
                    item["validation"] = {**validation, "status": "stale", "ready": False,
                                          "message": "Local settings changed after this test. Test the connection again"}
            except (FileNotFoundError, ValueError):
                pass
        models.append(item)
    return sorted(models, key=lambda model: model.get("priority", 0), reverse=True)


def _extract_model_id(text: str) -> str:
    text = text.strip()
    if text.startswith(("http://", "https://")):
        url = urlsplit(text)
        if url.hostname not in {"openrouter.ai", "www.openrouter.ai"}:
            raise ValueError("For provider URLs, supply the exact model_id, provider, and API base explicitly")
        if url.query or url.fragment or url.username or url.password:
            raise ValueError("Use a plain model page URL without credentials, query, or fragment")
        path = unquote(url.path).strip("/")
        if path.startswith("models/"):
            path = path[len("models/"):]
        if len(path.split("/")) != 2:
            raise ValueError("OpenRouter model URLs must preserve the organization/model path")
        return path
    return text


def _detect_provider(text: str) -> str | None:
    # Organization/model IDs are gateway IDs, even when the organization is
    # named anthropic/google. They must never use a native provider adapter.
    if "/" in text:
        return "openrouter"
    value = text.lower()
    for prefix, provider in (("claude-", "anthropic"), ("gemini-", "google"),
                             ("grok-", "xai"), ("deepseek-", "deepseek"),
                             ("gpt-", "openai")):
        if value.startswith(prefix):
            return provider
    return None


def _normalize_provider(provider: str | None) -> str | None:
    if not provider:
        return None
    return {"grok": "xai", "gemini": "google", "claude": "anthropic"}.get(provider.lower().strip(), provider.lower().strip())


async def _fetch_openrouter_info(model_id: str) -> dict | None:
    """Resolve exact IDs first; reject ambiguous or missing display names."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=3)) as client:
            response = await client.get("https://openrouter.ai/api/v1/models")
            response.raise_for_status()
            models = response.json().get("data", [])
    except httpx.HTTPError:
        return None
    query = model_id.casefold()
    matches = [model for model in models if model.get("id", "").casefold() == query]
    if not matches:
        matches = [model for model in models if model.get("name", "").casefold() == query]
    if not matches:
        matches = [model for model in models if query in (model.get("id", "") + " " + model.get("name", "")).casefold()]
    if len(matches) > 1:
        raise ValueError("Ambiguous model name; choose an exact ID: " + ", ".join(m["id"] for m in matches[:6]))
    if not matches:
        return None
    result = dict(matches[0])
    result["strengths"] = _infer_strengths(result.get("name", ""), result.get("description", ""))
    return result


async def lookup_and_register_model(name_or_url: str, *, provider: str | None = None,
                                    key: str | None = None, api_base: str | None = None,
                                    protocol: str | None = None, api_key_env: str | None = None,
                                    strengths: list[str] | None = None, priority: int | None = None,
                                    validate: bool = True) -> dict:
    model_id = _extract_model_id(name_or_url)
    provider = _normalize_provider(provider) or _detect_provider(model_id) or "openrouter"
    if name_or_url.startswith(("https://openrouter.ai/", "https://www.openrouter.ai/")) and provider != "openrouter":
        raise ValueError("An OpenRouter URL must use the openrouter provider")
    info = None
    if provider == "openrouter":
        info = await _fetch_openrouter_info(model_id)
        if info:
            model_id = info["id"]
        elif "/" not in model_id or re.search(r"\s", model_id):
            raise ValueError("Model name could not be resolved. Supply an exact provider/model ID or explicit native provider")
    model_key = key
    if not model_key:
        with _REGISTRY_LOCK:
            for existing_key, candidate in load_registry()["models"].items():
                if (candidate["id"] == model_id and candidate["provider"] == provider
                        and all(value is None or candidate.get(field) == value
                                for field, value in (("api_base", api_base), ("protocol", protocol), ("api_key_env", api_key_env)))):
                    model_key = existing_key
                    break
    model_key = model_key or re.sub(r"[^a-z0-9._-]+", "-", f"{provider}-{model_id}".lower()).strip("-")[:128]
    with _REGISTRY_LOCK:
        existing = load_registry()["models"].get(model_key)
    if existing:
        if (existing["id"] != model_id or existing["provider"] != provider
                or any(value is not None and existing.get(field) != value
                       for field, value in (("api_base", api_base), ("protocol", protocol), ("api_key_env", api_key_env)))):
            raise ValueError("This model key has different configuration; use configure_model")
        routing_changes = {}
        if strengths is not None and strengths != existing.get("strengths"):
            routing_changes["strengths"] = strengths
        if priority is not None and priority != existing.get("priority", 2):
            routing_changes["priority"] = priority
        entry = configure_model(model_key, **routing_changes) if routing_changes else {"key": model_key, **existing}
    else:
        entry = add_model(
            key=model_key, model_id=model_id, provider=provider,
            display_name=(info or {}).get("name", model_id),
            strengths=strengths or (info or {}).get("strengths", ["general"]),
            context_window=(info or {}).get("context_length") or 32000,
            api_base=api_base, protocol=protocol, api_key_env=api_key_env,
            free=bool(info and str(info.get("pricing", {}).get("prompt")) == "0"
                      and str(info.get("pricing", {}).get("completion")) == "0"), priority=priority if priority is not None else 2,
        )
    if validate:
        entry["validation"] = await test_model(model_key)
    return entry


async def test_model(key: str, *, timeout: float = 12) -> dict:
    """Verify authentication, model availability, and a native tool-result turn.

    The probe tool is handled locally with a constant result; it cannot run
    code, edit files, or execute any tool requested by the model.
    """
    model = load_registry()["models"].get(key)
    if not model:
        raise ValueError(f"Unknown model '{key}'")
    provider = get_provider(model["provider"], model=model)
    if not provider.is_available():
        result = {"status": "missing_credentials", "ready": False,
                  "message": f"Set {provider.api_key_env} in the local .env file; never send the key in chat"}
    else:
        tool = {"type": "function", "function": {
            "name": "asrar_connection_probe", "description": "Return a harmless connection check result",
            "parameters": {"type": "object", "properties": {"nonce": {"type": "integer"}}, "required": ["nonce"]},
        }}
        nonce = int.from_bytes(os.urandom(3), "big")
        messages = [Message("user", f"Call asrar_connection_probe with nonce={nonce}. After its result, reply ASRAR_MODEL_OK.")]
        async def probe():
            response = await provider.complete(messages=messages, model_id=model["id"], max_tokens=256, tools=[tool])
            if not response.success:
                return {"status": "failed", "ready": False, "message": response.error or "Provider request failed"}
            if (not response.tool_calls or any(call.get("name") != "asrar_connection_probe"
                    or call.get("args", {}).get("nonce") != nonce for call in response.tool_calls)):
                return {"status": "tool_calling_unverified", "ready": False, "message": "Completion worked, but the required tool call was not returned"}
            calls = [{"id": call["id"], "type": "function",
                      "function": {"name": call["name"], "arguments": json.dumps(call["args"])},
                      "thought_signature": call.get("thought_signature")}
                     for call in response.tool_calls]
            messages.append(Message("assistant", response.content or "", tool_calls=calls))
            messages.extend(Message("tool", "ASRAR_MODEL_OK", name=call["name"], tool_call_id=call["id"])
                            for call in response.tool_calls)
            final = None
            async for event in provider.stream_complete(messages=messages, model_id=model["id"], max_tokens=256):
                if event.get("type") == "done":
                    final = event.get("response")
            ready = bool(final and final.success and "ASRAR_MODEL_OK" in (final.content or ""))
            return {"status": "ready" if ready else "failed", "ready": ready,
                    "message": "Authentication, model completion, and streamed tool-result round trip passed" if ready
                    else (final.error if final else None) or "Streamed tool-result round trip did not complete"}
        try:
            result = await asyncio.wait_for(probe(), timeout=timeout)
        except TimeoutError:
            result = {"status": "timeout", "ready": False, "message": "Provider probe timed out"}
        except Exception as exc:
            from providers.transport import provider_error
            result = {"status": "failed", "ready": False, "message": provider_error(exc)}
    result["tested_at"] = datetime.now().isoformat()
    with _REGISTRY_LOCK:
        registry = load_registry()
        # A concurrent update must not receive the old connection's test result.
        if registry["models"].get(key) == model:
            registry["models"][key]["validation"] = result
            save_registry(registry)
    return result


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
