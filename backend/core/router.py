"""
Model Router
Given a classified task, picks the best free model to handle it.
Falls back gracefully if a model is unavailable.
"""

import json
import os
from pathlib import Path
from dataclasses import dataclass

from core.classifier import classify_task, ClassifiedTask


CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "models.json"


@dataclass
class RoutingDecision:
    task_type: str
    selected_model_key: str
    selected_model: dict
    fallback_chain: list[str]
    reason: str


def load_registry() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def get_available_models(registry: dict) -> set[str]:
    """Return model keys where the provider API key is set in env."""
    provider_key_map = {
        "anthropic": "ANTHROPIC_API_KEY",
        "google": "GOOGLE_API_KEY",
        "groq": "GROQ_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "mistral": "MISTRAL_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }

    available = set()
    for key, model in registry["models"].items():
        if not model.get("enabled", True):
            continue
        provider = model["provider"]
        env_key = provider_key_map.get(provider)
        if env_key and os.getenv(env_key):
            available.add(key)
        elif not env_key:
            available.add(key)  # no key needed

    return available


def route(user_input: str, force_model: str | None = None) -> RoutingDecision:
    """
    Main routing function.
    - Classifies the task
    - Picks the best available model
    - Returns routing decision with fallback chain
    """
    registry = load_registry()
    available = get_available_models(registry)
    task = classify_task(user_input)

    # Manual override
    if force_model:
        if force_model in registry["models"] and force_model in available:
            model = registry["models"][force_model]
            return RoutingDecision(
                task_type=task.task_type,
                selected_model_key=force_model,
                selected_model=model,
                fallback_chain=[],
                reason=f"Manually selected by user"
            )

    # Get preferred models for this task type
    task_map = registry["task_model_map"]
    preferred = task_map.get(task.task_type, task_map["general"])

    # Build fallback chain from preferred + all available sorted by priority
    all_available = sorted(
        [k for k in available],
        key=lambda k: registry["models"][k].get("priority", 0),
        reverse=True
    )

    fallback_chain = []
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
        f"Task classified as '{task.task_type}' (confidence: {task.confidence}). "
        f"'{selected_model['display_name']}' is the top model for this task type."
    )

    return RoutingDecision(
        task_type=task.task_type,
        selected_model_key=selected_key,
        selected_model=selected_model,
        fallback_chain=fallback_chain[1:],
        reason=reason
    )


if __name__ == "__main__":
    # Simulate with fake env vars for testing
    os.environ["ANTHROPIC_API_KEY"] = "test"
    os.environ["GROQ_API_KEY"] = "test"
    os.environ["GOOGLE_API_KEY"] = "test"
    os.environ["DEEPSEEK_API_KEY"] = "test"
    os.environ["MISTRAL_API_KEY"] = "test"

    test_cases = [
        "My PC keeps crashing with a blue screen",
        "Search online for AI news today",
        "Write a Python script to sort files",
        "Summarize this document",
        "Explain quantum entanglement in depth",
        "What time is it?",
    ]

    for inp in test_cases:
        decision = route(inp)
        print(f"\nTask   : {inp[:55]}...")
        print(f"Type   : {decision.task_type}")
        print(f"Model  : {decision.selected_model['display_name']}")
        print(f"Reason : {decision.reason}")
        print(f"Fallback chain: {decision.fallback_chain}")
