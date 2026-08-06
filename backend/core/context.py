"""
Context management — keep long conversations fast and within window limits.

Strategy:
  1. Keep the last KEEP_RECENT turns verbatim.
  2. Summarize older turns into a compact system-side note.
  3. Cache summaries per conversation so we don't re-summarize every call.
  4. Compress oversized tool results in history.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

logger = logging.getLogger("asrar.context")

KEEP_RECENT = 8          # full turns retained
SUMMARIZE_THRESHOLD = 12 # only summarize when history is longer than this
MAX_TOOL_RESULT_CHARS = 1500
MAX_SUMMARY_CHARS = 2000


@dataclass
class CompactHistory:
    messages: list[dict]
    summary: str | None = None
    truncated: bool = False


def _role_content(m) -> tuple[str, str]:
    if isinstance(m, dict):
        role = m.get("role", "user")
        content = m.get("content", "")
    else:
        role = getattr(m, "role", "user")
        content = getattr(m, "content", "")
    if isinstance(content, list):
        # Anthropic-style blocks
        parts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(b.get("text", ""))
                elif b.get("type") == "tool_result":
                    parts.append(f"[tool_result] {str(b.get('content', ''))[:200]}")
                elif b.get("type") == "tool_use":
                    parts.append(f"[tool_use {b.get('name')}]")
        content = " ".join(parts)
    return role, str(content or "")


def compress_tool_results_in_messages(messages: list[dict]) -> list[dict]:
    """Truncate very large tool results so context stays lean."""
    out = []
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            continue
        role = m.get("role")
        content = m.get("content", "")
        if role == "tool" and isinstance(content, str) and len(content) > MAX_TOOL_RESULT_CHARS:
            nm = dict(m)
            nm["content"] = content[:MAX_TOOL_RESULT_CHARS] + "\n…[truncated]"
            out.append(nm)
        else:
            out.append(m)
    return out


def _local_summarize(older: list) -> str:
    """Heuristic summary without an LLM call — always available."""
    lines = []
    for m in older:
        role, content = _role_content(m)
        content = content.replace("\n", " ").strip()
        if not content:
            continue
        if role == "user":
            lines.append(f"User asked: {content[:180]}")
        elif role == "assistant":
            lines.append(f"Assistant: {content[:180]}")
        elif role == "tool":
            lines.append(f"Tool result: {content[:100]}")
    text = " | ".join(lines)
    if len(text) > MAX_SUMMARY_CHARS:
        text = text[:MAX_SUMMARY_CHARS] + "…"
    return text or "(earlier conversation)"


async def _llm_summarize(older: list) -> str | None:
    """Optional fast-model summary. Returns None on failure."""
    try:
        from providers import get_provider
        from core.router import load_registry, get_available_models
        from providers.base import Message

        registry = load_registry()
        available = get_available_models(registry)
        for key in ("llama-70b", "mistral-small", "gemini-flash", "claude-haiku"):
            if key not in available:
                continue
            model = registry["models"][key]
            provider = get_provider(model["provider"])
            if not provider.is_available():
                continue
            transcript = []
            for m in older[-20:]:
                role, content = _role_content(m)
                transcript.append(f"{role}: {content[:400]}")
            prompt = (
                "Summarize this conversation so far in under 200 words. "
                "Keep: goals, decisions, file paths touched, open questions. "
                "Drop chit-chat.\n\n" + "\n".join(transcript)
            )
            resp = await provider.complete(
                messages=[Message(role="user", content=prompt)],
                model_id=model["id"],
                system="You write tight conversation summaries for an agent.",
                max_tokens=300,
            )
            if resp.success and resp.content:
                return resp.content.strip()[:MAX_SUMMARY_CHARS]
    except Exception as e:
        logger.debug("LLM summarize failed: %s", e)
    return None


def get_cached_summary(conversation_id: str | None) -> str | None:
    if not conversation_id:
        return None
    try:
        from core import memory
        with memory._conn() as c:
            row = c.execute(
                "SELECT summary FROM conversation_summaries WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        return row["summary"] if row else None
    except Exception:
        return None


def set_cached_summary(conversation_id: str, summary: str, up_to_message_count: int) -> None:
    try:
        from core import memory
        with memory._conn() as c:
            c.execute(
                """INSERT INTO conversation_summaries (conversation_id, summary, up_to_count, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(conversation_id) DO UPDATE SET
                       summary = excluded.summary,
                       up_to_count = excluded.up_to_count,
                       updated_at = excluded.updated_at""",
                (conversation_id, summary, up_to_message_count, memory._now()),
            )
    except Exception as e:
        logger.debug("cache summary failed: %s", e)


async def compact_history(
    history: list,
    conversation_id: str | None = None,
    use_llm: bool = True,
) -> CompactHistory:
    """
    Return a history list safe to send to the model.
    Injects a synthetic system/user summary turn for older context when needed.
    """
    if not history:
        return CompactHistory(messages=[])

    msgs = compress_tool_results_in_messages(
        [m if isinstance(m, dict) else {"role": getattr(m, "role", "user"), "content": getattr(m, "content", "")}
         for m in history]
    )

    if len(msgs) <= SUMMARIZE_THRESHOLD:
        return CompactHistory(messages=msgs)

    older = msgs[:-KEEP_RECENT]
    recent = msgs[-KEEP_RECENT:]

    summary = get_cached_summary(conversation_id)
    # Invalidate cache if conversation grew past what we summarized
    if conversation_id and summary:
        try:
            from core import memory
            with memory._conn() as c:
                row = c.execute(
                    "SELECT up_to_count FROM conversation_summaries WHERE conversation_id = ?",
                    (conversation_id,),
                ).fetchone()
            if row and row["up_to_count"] < len(msgs) - KEEP_RECENT:
                summary = None  # stale
        except Exception:
            pass

    if not summary:
        if use_llm:
            summary = await _llm_summarize(older)
        if not summary:
            summary = _local_summarize(older)
        if conversation_id:
            set_cached_summary(conversation_id, summary, len(older))

    # Prepend summary as a user/assistant pair so all providers accept it
    prefix = [
        {
            "role": "user",
            "content": (
                "[Earlier conversation summary — for context only]\n"
                f"{summary}\n"
                "[End summary. Continue from the recent messages below.]"
            ),
        },
        {"role": "assistant", "content": "Understood. I have the earlier context."},
    ]
    return CompactHistory(messages=prefix + recent, summary=summary, truncated=True)
