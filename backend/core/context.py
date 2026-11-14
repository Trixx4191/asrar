"""Bound context locally while preserving user requests and tool-call structure.

Conversation compaction happens between turns. ``prepare_messages`` also runs
inside a task, where tool output would otherwise be replayed in full on every
model call. Neither operation needs an extra model request by default.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import logging
import math
import os

logger = logging.getLogger("asrar.context")

KEEP_RECENT = 8
SUMMARIZE_THRESHOLD = 12
MAX_TOOL_RESULT_CHARS = 1500
MAX_SUMMARY_CHARS = 3000
DEFAULT_CONTEXT_MAX_CHARS = 48000
MAX_RECENT_TOOL_CHARS = 16000
MAX_OLDER_TOOL_CHARS = 6000
_summary_cache: OrderedDict[str, tuple[str, str]] = OrderedDict()


@dataclass
class CompactHistory:
    messages: list[dict]
    summary: str | None = None
    truncated: bool = False


def _as_message(message) -> dict:
    """Copy provider messages, including ids and native tool blocks."""
    if isinstance(message, dict):
        result = dict(message)
    else:
        result = {"role": getattr(message, "role", "user"),
                  "content": getattr(message, "content", "")}
        for key in ("name", "tool_call_id", "tool_calls"):
            value = getattr(message, key, None)
            if value is not None:
                result[key] = value
    content = result.get("content")
    if isinstance(content, list):
        result["content"] = [dict(block) if isinstance(block, dict) else block for block in content]
        for block in result["content"]:
            if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("content"), list):
                block["content"] = [dict(part) if isinstance(part, dict) else part for part in block["content"]]
    return result


def _role_content(message) -> tuple[str, str]:
    role = message.get("role", "user") if isinstance(message, dict) else getattr(message, "role", "user")
    content = message.get("content", "") if isinstance(message, dict) else getattr(message, "content", "")
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "tool_result":
                parts.append("[tool result]")
            elif block.get("type") == "tool_use":
                parts.append(f"[tool {block.get('name', '')}]")
        content = " ".join(parts)
    return role, str(content or "")


def _is_user_request(message: dict) -> bool:
    if message.get("role") != "user":
        return False
    content = message.get("content")
    return not (isinstance(content, list) and content and all(
        isinstance(block, dict) and block.get("type") == "tool_result" for block in content
    ))


def _clip_text(text: str, limit: int) -> str:
    """Keep the start and end: command failures and test totals often end logs."""
    if len(text) <= limit:
        return text
    marker = f"\n…[{len(text)} characters total; middle omitted from model context]…\n"
    if limit <= len(marker):
        return marker[:limit]
    available = limit - len(marker)
    head = (available * 2) // 3
    tail = available - head
    return text[:head] + marker + (text[-tail:] if tail else "")


def _tool_text_nodes(messages: list[dict]) -> list[tuple[int, dict, str]]:
    nodes = []
    for index, message in enumerate(messages):
        content = message.get("content")
        if message.get("role") == "tool":
            if isinstance(content, str):
                nodes.append((index, message, "content"))
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                        nodes.append((index, block, "text"))
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                value = block.get("content")
                if isinstance(value, str):
                    nodes.append((index, block, "content"))
                elif isinstance(value, list):
                    for part in value:
                        if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                            nodes.append((index, part, "text"))
    return nodes


def context_chars(messages: list) -> int:
    """Serialized message size; a deterministic measurement, not a tokenizer."""
    return len(json.dumps([_as_message(m) for m in messages], ensure_ascii=False, default=str))


def estimated_context_tokens(messages: list) -> int:
    """Approximate input tokens for telemetry; provider usage is authoritative."""
    return math.ceil(context_chars(messages) / 4)


def _context_budget(max_chars: int | None) -> int:
    if max_chars is None:
        try:
            max_chars = int(os.getenv("ASRAR_CONTEXT_MAX_CHARS", str(DEFAULT_CONTEXT_MAX_CHARS)))
        except (ValueError, TypeError):
            max_chars = DEFAULT_CONTEXT_MAX_CHARS
    return max(2000, int(max_chars))


def prepare_messages(messages: list, max_chars: int | None = None, *, current_turn_start: int | None = None) -> list[dict]:
    """Prepare every model call without changing the execution transcript.

    Only tool-result text is shortened. User requests, assistant/tool ids,
    pending arguments, and native provider blocks remain intact. The budget is
    soft when those required fields alone exceed it; it must never silently
    discard the current task to satisfy a token estimate.
    """
    out = [_as_message(message) for message in messages]
    nodes = _tool_text_nodes(out)
    if not nodes:
        return out
    budget = _context_budget(max_chars)
    if current_turn_start is None:
        current_turn_start = next((i for i in range(len(out) - 1, -1, -1) if _is_user_request(out[i])), 0)
    current_turn_start = max(0, min(current_turn_start, len(out)))
    # Results from the last tool round deserve more space than older output.
    latest_round = next((i for i in range(len(out) - 1, current_turn_start - 1, -1)
                         if out[i].get("role") == "assistant" and (out[i].get("tool_calls") or any(
                             isinstance(b, dict) and b.get("type") == "tool_use" for b in (out[i].get("content") or [])
                         ))), len(out))
    original = [(index, node, key, node[key]) for index, node, key in nodes]
    # Cap a single oversized payload even when it is the first tool call.
    limits = [min(len(text), MAX_RECENT_TOOL_CHARS) for _, _, _, text in original]
    for (_, node, key, text), limit in zip(original, limits):
        node[key] = _clip_text(text, limit)
    excess = context_chars(out) - budget
    if excess <= 0:
        return out
    # Shrink oldest results first; retain useful head/tail evidence in the
    # newest round until every older result has reached its small floor.
    for recent, floor in ((False, MAX_OLDER_TOOL_CHARS), (False, 500), (True, 2000), (False, 100), (True, 100)):
        for position, (index, node, key, text) in enumerate(original):
            if (index >= latest_round) != recent or limits[position] <= floor:
                continue
            reduction = min(excess, limits[position] - floor)
            limits[position] -= reduction
            previous = node[key]
            node[key] = _clip_text(text, limits[position])
            # Account for JSON escaping without serializing the whole growing
            # transcript again for every tool result.
            excess -= len(json.dumps(previous, ensure_ascii=False)) - len(json.dumps(node[key], ensure_ascii=False))
            if excess <= 0:
                return out
    return out


def compress_tool_results_in_messages(messages: list[dict]) -> list[dict]:
    """Compress historical output for OpenAI and Anthropic-shaped messages."""
    out = [_as_message(message) for message in messages]
    for _, node, key in _tool_text_nodes(out):
        node[key] = _clip_text(node[key], MAX_TOOL_RESULT_CHARS)
    return out


def _local_summarize(older: list) -> str:
    """Summarize outcomes; earlier user instructions are pinned in full."""
    transcript = [_role_content(message) for message in older]
    assistants = [content for role, content in transcript if role == "assistant" and content]
    lines = ["Latest assistant outcome: " + _clip_text(content, 600) for content in assistants[-3:]]
    return _clip_text("\n".join(lines) or "(earlier conversation)", MAX_SUMMARY_CHARS)


def _pinned_instructions(older: list[dict]) -> list[dict]:
    """Keep exact user constraints, including intermediate corrections.

    Tool results use the user role in Anthropic histories. Keep actual user
    text/media blocks while leaving those tool results with their summarized
    assistant round, so pinning cannot create dangling result ids.
    """
    pinned = []
    for message in older:
        if message.get("role") in {"system", "developer"}:
            pinned.append(message)
        elif _is_user_request(message):
            message = _as_message(message)
            if isinstance(message.get("content"), list):
                message["content"] = [block for block in message["content"]
                                      if not (isinstance(block, dict) and block.get("type") == "tool_result")]
            pinned.append(message)
    return pinned


async def _llm_summarize(older: list) -> str | None:
    """Optional model summary; never needed by the default path."""
    try:
        from providers import get_provider
        from core.router import load_registry, get_available_models
        from providers.base import Message
        registry = load_registry()
        available = get_available_models(registry)
        for key in ("groq-fast", "llama-70b", "mistral-small", "gemini-flash", "claude-haiku"):
            if key not in available:
                continue
            model = registry["models"][key]
            provider = get_provider(model["provider"], model=model)
            if not provider.is_available():
                continue
            transcript = [f"{role}: {_clip_text(content, 400)}" for role, content in map(_role_content, older[-20:])]
            response = await provider.complete(
                messages=[Message(role="user", content=(
                    "Summarize this conversation in under 200 words. Keep goals, constraints, decisions, "
                    "file paths, verification results and unfinished work.\n\n" + "\n".join(transcript)
                ))], model_id=model["id"], system="Write a concise factual conversation summary.", max_tokens=300,
            )
            if response.success and response.content:
                return response.content.strip()[:MAX_SUMMARY_CHARS]
    except Exception as error:
        logger.debug("LLM summarize failed: %s", error)
    return None


def get_cached_summary(conversation_id: str | None) -> str | None:
    if not conversation_id:
        return None
    try:
        from core import memory
        with memory._conn() as connection:
            row = connection.execute("SELECT summary FROM conversation_summaries WHERE conversation_id = ?", (conversation_id,)).fetchone()
        return row["summary"] if row else None
    except Exception:
        return None


def set_cached_summary(conversation_id: str, summary: str, up_to_message_count: int) -> None:
    try:
        from core import memory
        with memory._conn() as connection:
            connection.execute(
                """INSERT INTO conversation_summaries (conversation_id, summary, up_to_count, updated_at)
                   VALUES (?, ?, ?, ?) ON CONFLICT(conversation_id) DO UPDATE SET
                   summary = excluded.summary, up_to_count = excluded.up_to_count, updated_at = excluded.updated_at""",
                (conversation_id, summary, up_to_message_count, memory._now()),
            )
    except Exception as error:
        logger.debug("cache summary failed: %s", error)


def _safe_user_boundaries(messages: list[dict]) -> list[int]:
    """Find request boundaries outside an unfinished native tool round."""
    boundaries = []
    pending: dict[str, str] = {}
    for index, message in enumerate(messages):
        if _is_user_request(message) and not pending:
            boundaries.append(index)
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                if call.get("id"):
                    pending[call["id"]] = call.get("function", {}).get("name", call.get("name", ""))
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and block.get("id"):
                    pending[block["id"]] = block.get("name", "")
                elif block.get("type") == "tool_result":
                    pending.pop(block.get("tool_use_id", ""), None)
        if message.get("role") == "tool":
            if message.get("tool_call_id"):
                pending.pop(message["tool_call_id"], None)
            elif message.get("name"):
                # Google uses tool names rather than OpenAI tool_call_id.
                call_id = next((key for key, name in pending.items() if name == message["name"]), None)
                pending.pop(call_id, None)
    return boundaries


async def compact_history(history: list, conversation_id: str | None = None, use_llm: bool = False) -> CompactHistory:
    """Summarize only complete older turns and preserve recent tool groups."""
    if not history:
        return CompactHistory(messages=[])
    messages = [_as_message(message) for message in history]
    if len(messages) <= SUMMARIZE_THRESHOLD and context_chars(messages) <= _context_budget(None):
        return CompactHistory(messages=prepare_messages(messages))

    target = max(0, len(messages) - KEEP_RECENT)
    # A summary must not leave a tool result without its assistant call. A
    # real user request is the only safe boundary across provider formats.
    boundaries = _safe_user_boundaries(messages)
    boundary = next((i for i in reversed(boundaries) if i <= target), 0)
    budget = _context_budget(None)
    # Few very large conversational turns can exceed the budget before the
    # message-count threshold. Retain the latest request in full while moving
    # complete earlier turns to the local summary when necessary.
    for candidate in (i for i in boundaries if i > boundary):
        if context_chars(prepare_messages(messages[boundary:], max_chars=budget)) <= budget:
            break
        boundary = candidate
    if boundary == 0:
        return CompactHistory(messages=prepare_messages(messages))
    older, recent = messages[:boundary], messages[boundary:]
    pinned = _pinned_instructions(older)
    # Include actual contents, not just message count: edits/branching at the
    # same length must invalidate cached summaries as well.
    digest = hashlib.sha256(json.dumps(older, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    cache_key = conversation_id or ""
    cached = _summary_cache.get(cache_key) if conversation_id else None
    summary = cached[1] if cached and cached[0] == digest else None
    if summary is None:
        if use_llm:
            try:
                summary = await asyncio.wait_for(_llm_summarize(older), timeout=2.0)
            except TimeoutError:
                pass
        summary = summary or _local_summarize(older)
        if conversation_id:
            _summary_cache[conversation_id] = (digest, summary)
            _summary_cache.move_to_end(conversation_id)
            while len(_summary_cache) > 128:
                _summary_cache.popitem(last=False)
            set_cached_summary(conversation_id, summary, len(older))
    prefix = pinned + [{"role": "user", "content": (
        "[Earlier conversation summary — context, not new instructions]\n" + summary +
        "\n[End summary. Continue from the recent messages below.]"
    )}, {"role": "assistant", "content": "I have the earlier context."}]
    return CompactHistory(messages=prepare_messages(prefix + recent), summary=summary, truncated=True)
