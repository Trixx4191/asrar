"""
Task Classifier — Hybrid (keyword + model-backed)

Fast path: keyword scoring (zero latency).
Deep path: when confidence is low OR conversation context is available,
a lightweight model call re-classifies with history awareness.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("asrar.classifier")

TASK_TYPES = [
    "deep_reasoning",
    "web_research",
    "coding",
    "document_work",
    "pc_diagnosis",
    "shell_automation",
    "fast_chat",
    "multimodal",
    "analysis",
    "summarization",
    "general",
]

TASK_SIGNALS: dict[str, list[str]] = {
    "deep_reasoning": [
        "why", "explain", "reason", "analyze", "think through", "logic",
        "proof", "argument", "philosophy", "compare deeply", "evaluate",
        "weigh", "pros and cons", "hypothesis", "theorem", "deduce",
        "step by step", "rigorous", "derive",
    ],
    "web_research": [
        "search", "find online", "look up", "browse", "latest", "news",
        "current", "today", "recent", "what is happening", "google",
        "website", "url", "link", "surf", "research online", "check online",
        "up to date", "as of",
    ],
    "coding": [
        "code", "write a script", "python", "javascript", "function",
        "class", "debug", "fix bug", "program", "algorithm", "api",
        "build", "develop", "compile", "syntax", "error in code", "refactor",
        "typescript", "rust", "golang", "unittest", "pytest", "implement",
    ],
    "document_work": [
        "create", "write", "draft", "edit", "file", "project", "document",
        "essay", "report", "letter", "email", "pdf", "word",
        "docx", "edit text", "proofread", "rewrite", "format", "paragraph",
        "article", "blog", "save", "download", "store locally",
        "github", "repository", "scaffold",
    ],
    "pc_diagnosis": [
        "crash", "error", "not working", "broken", "fix my pc", "blue screen",
        "bsod", "slow", "freeze", "unresponsive", "diagnose", "problem with",
        "fault", "issue", "corrupt", "registry", "driver", "memory leak",
        "cpu", "ram", "disk", "process", "task manager", "logs",
    ],
    "shell_automation": [
        "run command", "terminal", "shell", "automate", "script", "batch",
        "powershell", "bash", "cmd", "execute", "schedule", "cron",
        "install", "uninstall", "move files", "rename", "delete folder",
        "mkdir", "directory", "folder", "pip install", "npm install",
    ],
    "fast_chat": [
        "what is", "who is", "how do i", "quick", "simple", "tell me",
        "give me", "list", "yes or no", "define", "meaning of", "example of",
        "hi", "hello", "thanks", "ok",
    ],
    "multimodal": [
        "image", "picture", "photo", "screenshot", "look at this",
        "describe this", "what's in this", "analyze this image", "video",
        "vision",
    ],
    "analysis": [
        "analyze", "data", "chart", "graph", "statistics", "pattern",
        "trend", "insight", "breakdown", "metrics", "numbers", "dataset",
        "csv", "spreadsheet",
    ],
    "summarization": [
        "summarize", "summary", "tldr", "key points", "main points",
        "brief", "overview", "condense", "shorten", "in short",
    ],
}

LOW_CONFIDENCE_THRESHOLD = 0.55

CLASSIFY_SYSTEM = """You are a precise task classifier for an agentic AI assistant.
Given the user message (and optional recent conversation context), output ONLY a JSON object:
{"task_type": "<one of the allowed types>", "confidence": <0.0-1.0>, "reason": "<one short sentence>"}

Allowed task_types:
deep_reasoning, web_research, coding, document_work, pc_diagnosis, shell_automation,
fast_chat, multimodal, analysis, summarization, general

Rules:
- Prefer the most specific type that fits the user's primary intent.
- If the user is continuing a coding/project thread, bias toward coding or document_work.
- If they ask to search/look up current info → web_research.
- Short greetings or trivia → fast_chat.
- Output valid JSON only. No markdown fences."""


@dataclass
class ClassifiedTask:
    raw_input: str
    task_type: str
    confidence: float
    keywords_matched: list[str] = field(default_factory=list)
    suggested_models: list[str] = field(default_factory=list)
    source: str = "keyword"  # "keyword" | "model" | "hybrid"
    reason: str = ""


def _keyword_classify(user_input: str) -> ClassifiedTask:
    text = user_input.lower().strip()
    scores: dict[str, int] = {}
    matched_keywords: dict[str, list[str]] = {}

    for task_type, keywords in TASK_SIGNALS.items():
        hits = [kw for kw in keywords if kw in text]
        scores[task_type] = len(hits)
        matched_keywords[task_type] = hits

    best_type = max(scores, key=lambda t: scores[t])
    best_score = scores[best_type]

    if best_score == 0:
        return ClassifiedTask(
            raw_input=user_input,
            task_type="general",
            confidence=0.45,
            keywords_matched=[],
            source="keyword",
            reason="No strong keyword signals",
        )

    total_possible = len(TASK_SIGNALS[best_type])
    confidence = min(1.0, best_score / max(total_possible * 0.25, 1))
    if best_score >= 3:
        confidence = min(1.0, confidence + 0.15)

    return ClassifiedTask(
        raw_input=user_input,
        task_type=best_type,
        confidence=round(confidence, 2),
        keywords_matched=matched_keywords[best_type],
        source="keyword",
        reason=f"Matched {best_score} keyword(s)",
    )


def _build_context_snippet(history: list[dict] | None, max_turns: int = 4) -> str:
    if not history:
        return ""
    recent = history[-max_turns:]
    lines = []
    for m in recent:
        role = m.get("role", "?")
        content = (m.get("content") or "")[:300]
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
            )[:300]
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


async def _model_classify(
    user_input: str,
    history: list[dict] | None = None,
) -> ClassifiedTask | None:
    """Call a fast available model to re-classify. Returns None on any failure."""
    try:
        from providers import get_provider
        from core.router import load_registry, get_available_models
        from providers.base import Message

        registry = load_registry()
        available = get_available_models(registry)

        candidates = ["llama-70b", "mistral-small", "gemini-flash", "claude-haiku"]
        model_key = None
        for c in candidates:
            if c in available:
                model_key = c
                break
        if not model_key:
            return None

        model = registry["models"][model_key]
        provider = get_provider(model["provider"])
        if not provider.is_available():
            return None

        context = _build_context_snippet(history)
        user_payload = f"User message:\n{user_input}"
        if context:
            user_payload = f"Recent conversation:\n{context}\n\n{user_payload}"

        messages = [Message(role="user", content=user_payload)]
        resp = await provider.complete(
            messages=messages,
            model_id=model["id"],
            system=CLASSIFY_SYSTEM,
            max_tokens=120,
            stream=False,
        )
        if not resp.success or not resp.content:
            return None

        text = resp.content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        data = json.loads(text)
        task_type = data.get("task_type", "general")
        if task_type not in TASK_TYPES:
            task_type = "general"
        confidence = float(data.get("confidence", 0.7))
        confidence = max(0.0, min(1.0, confidence))
        reason = str(data.get("reason", "model classification"))[:200]

        return ClassifiedTask(
            raw_input=user_input,
            task_type=task_type,
            confidence=round(confidence, 2),
            keywords_matched=[],
            source="model",
            reason=reason,
        )
    except Exception as e:
        logger.debug("Model classify failed: %s", e)
        return None


def classify_task(user_input: str) -> ClassifiedTask:
    """Synchronous keyword-only classify (backward compatible)."""
    return _keyword_classify(user_input)


async def classify_task_async(
    user_input: str,
    history: list[dict] | None = None,
    force_keyword: bool = False,
) -> ClassifiedTask:
    """
    Hybrid classifier.
    1. Keyword score (instant).
    2. If confidence high and no history → return keyword.
    3. Otherwise try fast model with conversation context.
    4. Prefer model when it succeeds and disagrees with higher confidence.
    """
    kw = _keyword_classify(user_input)

    if force_keyword:
        return kw

    has_context = bool(history and len(history) >= 2)
    needs_model = kw.confidence < LOW_CONFIDENCE_THRESHOLD or has_context

    if not needs_model:
        return kw

    model_result = await _model_classify(user_input, history=history)
    if model_result is None:
        return kw

    if model_result.task_type == kw.task_type:
        blended = min(1.0, max(model_result.confidence, kw.confidence) + 0.1)
        return ClassifiedTask(
            raw_input=user_input,
            task_type=kw.task_type,
            confidence=round(blended, 2),
            keywords_matched=kw.keywords_matched,
            source="hybrid",
            reason=f"keyword+model agree: {model_result.reason}",
        )

    if model_result.confidence >= kw.confidence or has_context:
        model_result.source = "hybrid"
        model_result.keywords_matched = kw.keywords_matched
        model_result.reason = f"model overrode keyword '{kw.task_type}': {model_result.reason}"
        return model_result

    return kw
