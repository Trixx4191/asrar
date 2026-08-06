"""
Model Router
Given a classified task, picks the best free model to handle it.
Falls back gracefully if a model is unavailable.
Supports hybrid classification with conversation context.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from dataclasses import dataclass

from core.classifier import classify_task, classify_task_async, ClassifiedTask


CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "models.json"


@dataclass
class RoutingDecision:
    task_type: str
    selected_model_key: str
    selected_model: dict
    fallback_chain: list[str]
    reason: str
    sticky: bool = False
    classification_source: str = "keyword"
    confidence: float = 0.5


def load_registry() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def get_available_models(registry: dict) -> set[str]:
    provider_key_map = {
        "anthropic": "ANTHROPIC_API_KEY",
        "google": "GOOGLE_API_KEY",
        "groq": "GROQ_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "mistral": "MISTRAL_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "qwen": "QWEN_API_KEY",
        "kimi": "KIMI_API_KEY",
    }

    available: set[str] = set()
    for key, model in registry["models"].items():
        if not model.get("enabled", True):
            continue
        provider = (model.get("provider") or "").lower()
        env_key = provider_key_map.get(provider)
        if env_key:
            if os.getenv(env_key):
                available.add(key)
        else:
            available.add(key)
    return available


def _build_decision(
    task: ClassifiedTask,
    registry: dict,
    available: set[str],
    force_model: str | None = None,
) -> RoutingDecision:
    if force_model:
        if force_model in registry["models"] and force_model in available:
            model = registry["models"][force_model]
            return RoutingDecision(
                task_type=task.task_type,
                selected_model_key=force_model,
                selected_model=model,
                fallback_chain=[],
                reason="Manually selected by user",
                classification_source=task.source,
                confidence=task.confidence,
            )

    task_map = registry["task_model_map"]
    preferred = task_map.get(task.task_type, task_map.get("general", []))

    all_available = sorted(
        [k for k in available],
        key=lambda k: registry["models"][k].get("priority", 0),
        reverse=True,
    )

    fallback_chain: list[str] = []
    for m in preferred:
        if m in available:
            fallback_chain.append(m)
    for m in all_available:
        if m not in fallback_chain:
            fallback_chain.append(m)

    if not fallback_chain:
        raise RuntimeError("No available models found. Check your API keys in .env")

    selected_key = fallback_chain[0]
    selected_model = registry["models"][selected_key]

    reason = (
        f"Task classified as '{task.task_type}' "
        f"(confidence: {task.confidence}, source: {task.source}). "
        f"'{selected_model['display_name']}' is the top model for this task type."
    )
    if task.reason:
        reason += f" Classifier note: {task.reason}"

    return RoutingDecision(
        task_type=task.task_type,
        selected_model_key=selected_key,
        selected_model=selected_model,
        fallback_chain=fallback_chain[1:],
        reason=reason,
        classification_source=task.source,
        confidence=task.confidence,
    )


def route(user_input: str, force_model: str | None = None) -> RoutingDecision:
    """Sync route (keyword-only classification). Kept for backward compatibility."""
    registry = load_registry()
    available = get_available_models(registry)
    task = classify_task(user_input)
    return _build_decision(task, registry, available, force_model)


async def route_async(
    user_input: str,
    force_model: str | None = None,
    history: list[dict] | None = None,
) -> RoutingDecision:
    """Async route with hybrid classification + conversation context."""
    registry = load_registry()
    available = get_available_models(registry)
    task = await classify_task_async(user_input, history=history)
    return _build_decision(task, registry, available, force_model)
