"""
Task Classifier — Hybrid (keyword + model-backed)

Fast path: keyword scoring and short follow-up context (no model call).
Deep path: optional, bounded model classification for uncertain requests.
"""

from __future__ import annotations

import json
import asyncio
import os
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

# Routing looks at the request, not every token of a pasted source file. Keep
# both ends so a question following a large quote is still visible.
MAX_CLASSIFICATION_CHARS = 6000
_KEYWORD_PATTERNS = {
    task: [(keyword, re.compile(r"(?<!\w)" + re.escape(keyword) + r"(?!\w)"))
           for keyword in keywords]
    for task, keywords in TASK_SIGNALS.items()
}
_POLITE_PREFIX = re.compile(
    r"^(?:(?:please|can you|could you|would you|help me|i need you to|i want you to|now|then|also)\s+)+"
)

# Match the operation before counting incidental nouns in its subject. A
# report about Python is document work; building a website is coding.
_PRIMARY_INTENTS = [
    (r"^(?:summari[sz]e|summary|tldr|condense|shorten)\b", "summarization"),
    (r"^(?:diagnose|troubleshoot)\b|^fix my (?:pc|computer)\b", "pc_diagnosis"),
    (r"^(?:search|find|look for)\b.*\b(?:codebase|repository|repo|source code)\b", "coding"),
    (r"^(?:search|browse|look up|find online|research online|check online)\b", "web_research"),
    (r"^(?:edit|fix|debug|refactor|review|update)\b.*\.(?:py|js|jsx|ts|tsx|rs|go|java|cpp|c|h|rb|php|swift)\b", "coding"),
    (r"^(?:write|draft|edit|create|rewrite|format|proofread)\b.*\b(?:essay|report|letter|email|pdf|docx|document|article|blog|paragraph)\b", "document_work"),
    (r"^(?:fix|debug|repair|refactor|review|optimi[sz]e|implement)\b.*\b(?:tests?|ci|code|bug|function|script|app|application|ui|frontend|backend|architecture|api|website|page|component)\b", "coding"),
    (r"^(?:build|develop|implement|scaffold|refactor|compile)\b", "coding"),
    (r"^(?:write|create|edit|add|update)\b.*\b(?:python|javascript|typescript|rust|golang|function|class|code|script|app|application|website|webpage|component|api)\b", "coding"),
    (r"^(?:why|how)\b.*\b(?:code|function|app|application|test|script|api)\b.*\b(?:crash(?:es|ing)?|fail(?:s|ing)?|broken|error|slow|not working)\b", "coding"),
    (r"^make\b.*\b(?:app|application|website|code|ui)\b.*\b(?:fast(?:er)?|responsive|work)\b", "coding"),
    (r"^(?:run|execute|install|uninstall|automate|schedule|rename|move|delete)\b", "shell_automation"),
    (r"^(?:analy[sz]e|plot|compare)\b.*\b(?:csv|dataset|data|statistics|metrics|spreadsheet|chart)\b", "analysis"),
    (r"^(?:describe|analy[sz]e|inspect)\b.*\b(?:image|picture|photo|screenshot|video)\b", "multimodal"),
    (r"^(?:prove|derive|deduce|think through)\b", "deep_reasoning"),
    (r"^(?:create|open|update|merge)\b.*\b(?:pull request|github issue)\b", "document_work"),
    (r"^(?:hi|hello|hey|thanks|thank you)[!.\s]*$", "fast_chat"),
]
_PRIMARY_INTENTS = [(re.compile(pattern), task) for pattern, task in _PRIMARY_INTENTS]
_REFERENTIAL_FOLLOWUP = re.compile(
    r"^(?:what about|how about|and(?: then)?|also|now|try|instead|again|same|"
    r"(?:write|build|implement|do|run|edit|fix|change|make|update|use) (?:it|that|this|them|those|these)|"
    r"the (?:first|second|other|last)|it|that|this|those|these)\b"
)

_CONTINUATION_RE = re.compile(
    r"(?:(?:yes|yeah|yep|ok|okay|sure|alright)(?: please)?|sounds good|"
    r"(?:(?:yes|yeah|yep|ok|okay|sure|alright) )?(?:please )?"
    r"(?:go ahead|go on|continue|proceed|do it|do that|keep going|carry on)(?: please)?)"
)

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
    explicit_intent: bool = False


def _classification_text(user_input: str) -> str:
    text = user_input.lower().strip()
    if len(text) > MAX_CLASSIFICATION_CHARS:
        return text[:4000] + "\n" + text[-1999:]
    return text


def _message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(block.get("text", "") for block in content
                        if isinstance(block, dict) and isinstance(block.get("text"), str))
    return ""


def is_referential_followup(user_input: str) -> bool:
    """Whether the request refers back to the active work rather than naming a new task."""
    normalized = re.sub(r"[\s,!.]+", " ", user_input.lower()).strip()
    normalized = _POLITE_PREFIX.sub("", normalized)
    return bool(_CONTINUATION_RE.fullmatch(normalized) or _REFERENTIAL_FOLLOWUP.search(normalized))


def _keyword_classify(user_input: str) -> ClassifiedTask:
    text = _classification_text(user_input)
    scores: dict[str, int] = {}
    matched_keywords: dict[str, list[str]] = {}

    for task_type, patterns in _KEYWORD_PATTERNS.items():
        hits = [keyword for keyword, pattern in patterns if pattern.search(text)]
        scores[task_type] = len(hits)
        matched_keywords[task_type] = hits

    primary = _POLITE_PREFIX.sub("", text)
    for pattern, task_type in _PRIMARY_INTENTS:
        if pattern.search(primary):
            return ClassifiedTask(
                raw_input=user_input, task_type=task_type, confidence=0.9,
                keywords_matched=matched_keywords[task_type], source="keyword",
                reason="Explicit request operation", explicit_intent=True,
            )

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
        content = _message_text(m.get("content"))[:300]
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
        from core import orchestrator

        registry = load_registry()
        available = get_available_models(registry)

        candidates = ["groq-fast", "llama-70b", "mistral-small", "gemini-flash", "claude-haiku"]
        model_key = None
        for c in candidates:
            if (c in available
                    and registry["models"][c].get("validation", {}).get("status")
                    not in {"failed", "timeout", "tool_calling_unverified"}
                    and orchestrator.is_available(registry["models"][c]["provider"])):
                model_key = c
                break
        if not model_key:
            return None

        model = registry["models"][model_key]
        provider = get_provider(model["provider"], model=model)
        if not provider.is_available():
            return None

        context = _build_context_snippet(history)
        user_payload = f"User message:\n{_classification_text(user_input)}"
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
    2. Retain the recent task for short continuations; return confident local intent.
    3. In hybrid mode, try a fast model only for uncertain requests.
    4. Prefer model when it succeeds and disagrees with higher confidence.
    """
    kw = _keyword_classify(user_input)

    normalized = re.sub(r"[\s,!.]+", " ", user_input.lower()).strip()
    is_continuation = bool(_CONTINUATION_RE.fullmatch(normalized))
    is_referential = bool(_REFERENTIAL_FOLLOWUP.search(normalized))
    if history and not kw.explicit_intent and len(user_input.split()) <= 16 and (is_continuation or is_referential):
        # Only the latest substantive user request owns a continuation. Do not
        # reach back past a new, unclassified topic to an unrelated older task.
        for message in reversed(history[-16:]):
            if message.get("role") != "user":
                continue
            previous_input = _message_text(message.get("content"))
            previous_normalized = re.sub(r"[\s,!.]+", " ", previous_input.lower()).strip()
            if _CONTINUATION_RE.fullmatch(previous_normalized):
                continue
            previous = _keyword_classify(previous_input)
            if previous.task_type != "general":
                previous.raw_input = user_input
                previous.explicit_intent = False
                previous.reason = "Short follow-up: retained recent user task type"
                return previous
            break

    # Routing must not add an LLM round trip to every user message. Hybrid
    # classification is opt-in and bounded independently of provider timeouts.
    if force_keyword or os.getenv("ASRAR_ROUTING_MODE", "fast").lower() != "hybrid":
        return kw

    has_context = bool(history and len(history) >= 2)
    if kw.confidence >= LOW_CONFIDENCE_THRESHOLD:
        return kw

    try:
        timeout = max(0.05, min(float(os.getenv("ASRAR_ROUTING_TIMEOUT", "1.5")), 5.0))
        model_result = await asyncio.wait_for(_model_classify(user_input, history=history), timeout)
    except (TimeoutError, ValueError):
        return kw
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
