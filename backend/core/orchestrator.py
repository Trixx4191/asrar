"""
Orchestrator — circuit breaker, retry/backoff, and concurrent tool
execution for the agent loop.
backend/core/orchestrator.py

This used to be a passthrough scaffold that wrapped agent.run_task()
and did nothing else — every route imported agent directly, so nothing
ever called it. That's the same "note nobody reads" problem fixed
elsewhere in this codebase: a module that looks like a feature but isn't
wired to anything is functionally not a feature. This version has three
real jobs and is imported directly by agent.py's and chat.py's model
loops, not left as unused dead code:

  1. Circuit breaker per provider — after repeated consecutive failures,
     stop trying that provider for a cooldown window instead of paying
     full request latency to fail every single time. Automatically
     half-opens after the cooldown to test recovery, and backs off
     further (up to a cap) if the trial fails again.

  2. Retry with backoff for transient provider errors (429 rate limits,
     502/503/504) on the SAME model before giving up on it and falling
     back to a lower-priority model — a rate-limit blip shouldn't demote
     the whole request to a worse model. Non-retryable errors (400/401/
     403/404) return immediately; retrying those just wastes time for
     the same answer.

  3. Concurrent execution of independent read-only tool calls
     (search_code, read_file, list_dir, web_search, fetch_page,
     diagnose_system) via asyncio.gather, while mutating tools
     (write_file, edit_file, run_command, run_tests, execute_code,
     create_project, update_plan) still run strictly in order — nothing
     that depends on a previous tool's side effect can race against it.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass


# ─────────────────────────────────────────────────────────────
# Circuit breaker
# ─────────────────────────────────────────────────────────────

FAILURE_THRESHOLD = 3        # consecutive failures before opening the circuit
BASE_COOLDOWN_SECONDS = 30.0  # initial time to wait before a half-open trial
BACKOFF_MULTIPLIER = 2.0      # cooldown grows each time a half-open trial fails
MAX_COOLDOWN_SECONDS = 300.0  # 5 minutes, cap on how long a provider stays skipped


@dataclass
class _CircuitState:
    consecutive_failures: int = 0
    state: str = "closed"        # closed | open | half_open
    opened_at: float = 0.0
    cooldown: float = BASE_COOLDOWN_SECONDS


_circuits: dict[str, _CircuitState] = {}


def _get_circuit(provider_name: str) -> _CircuitState:
    return _circuits.setdefault(provider_name, _CircuitState())


def is_available(provider_name: str) -> bool:
    """Whether the circuit breaker will allow a call to this provider right
    now. Independent of provider.is_available(), which only checks whether
    an API key is configured -- this tracks live failure behavior."""
    c = _get_circuit(provider_name)
    if c.state == "closed":
        return True
    if c.state == "open":
        if time.monotonic() - c.opened_at >= c.cooldown:
            c.state = "half_open"
            return True
        return False
    return True  # half_open: let exactly one trial through


def record_success(provider_name: str) -> None:
    c = _get_circuit(provider_name)
    c.consecutive_failures = 0
    c.state = "closed"
    c.cooldown = BASE_COOLDOWN_SECONDS


def record_failure(provider_name: str) -> None:
    c = _get_circuit(provider_name)
    c.consecutive_failures += 1
    if c.state == "half_open":
        c.cooldown = min(c.cooldown * BACKOFF_MULTIPLIER, MAX_COOLDOWN_SECONDS)
        c.state = "open"
        c.opened_at = time.monotonic()
    elif c.consecutive_failures >= FAILURE_THRESHOLD:
        c.state = "open"
        c.opened_at = time.monotonic()


def circuit_status(provider_name: str) -> dict:
    c = _get_circuit(provider_name)
    return {
        "provider": provider_name,
        "state": c.state,
        "consecutive_failures": c.consecutive_failures,
        "cooldown_seconds": c.cooldown,
    }


def reset_circuit(provider_name: str) -> None:
    """Force a provider back to a clean closed state. Used by tests, and
    available for an admin/debug endpoint if one's ever wired up."""
    _circuits[provider_name] = _CircuitState()


# ─────────────────────────────────────────────────────────────
# Retry with backoff for transient provider errors
# ─────────────────────────────────────────────────────────────

_RETRYABLE_STATUS = {"429", "500", "502", "503", "504"}
_STATUS_RE = re.compile(r"Provider HTTP (\d+)")
_RETRY_AFTER_RE = re.compile(r"retry-after=(\d+(?:\.\d+)?)")


def is_provider_error(text: str) -> bool:
    return isinstance(text, str) and text.strip().startswith("Provider HTTP")


def is_retryable_error(text: str) -> bool:
    if not is_provider_error(text):
        return False
    m = _STATUS_RE.search(text)
    return bool(m and m.group(1) in _RETRYABLE_STATUS)


def retry_delay_seconds(attempt: int, text: str = "") -> float:
    """Honor a server-supplied Retry-After if the provider sent one, else
    exponential backoff (1s, 2s, 4s...) capped at 8s so a retry loop can't
    stall a request for minutes."""
    m = _RETRY_AFTER_RE.search(text or "")
    if m:
        return min(float(m.group(1)), 15.0)
    return min(2 ** attempt, 8)


async def call_with_retry(fn, *, max_retries: int = 2, on_retry=None):
    """Call an async no-arg callable returning (text, tool_calls) -- the
    same shape as _llm_call_with_tools. Retries only retryable provider
    errors, with backoff, up to max_retries times, then returns whatever
    the last attempt produced so the caller's existing fallback-to-next-
    model logic still runs unchanged."""
    attempt = 0
    while True:
        text, tool_calls = await fn()
        if tool_calls or not is_retryable_error(text) or attempt >= max_retries:
            return text, tool_calls
        delay = retry_delay_seconds(attempt, text)
        if on_retry:
            on_retry(attempt, delay, text)
        await asyncio.sleep(delay)
        attempt += 1


# ─────────────────────────────────────────────────────────────
# Concurrent tool execution
# ─────────────────────────────────────────────────────────────

READ_ONLY_TOOLS = {
    "read_file", "list_dir", "web_search", "fetch_page",
    "diagnose_system", "search_code", "find_files",
}


async def run_tool_calls(tool_calls: list[dict], call_tool_fn) -> list[str]:
    """Execute one turn's tool calls. Consecutive read-only calls run
    concurrently via asyncio.gather; anything else (write_file, edit_file,
    run_command, run_tests, execute_code, create_project, update_plan)
    runs strictly in order, both relative to each other and relative to
    surrounding read-only batches, so a write can't race a read of the
    same file and two writes can't race each other.

    call_tool_fn(name, args, index) -> awaitable[str]. `index` is this
    call's position in the original tool_calls list — pass a closure with
    conversation_id already bound. The index lets a caller capture a
    per-call side effect (e.g. a diff produced by that specific write)
    at the moment that call actually executes, rather than trying to
    recover it after the whole batch finishes, when a later call may have
    already overwritten whatever single-slot state held it.

    Returns results in the SAME order as tool_calls, regardless of which
    ones ran concurrently, so callers can zip them back to tool_use ids.
    """
    results: list[str | None] = [None] * len(tool_calls)
    i = 0
    while i < len(tool_calls):
        if tool_calls[i]["name"] in READ_ONLY_TOOLS:
            batch_start = i
            batch = []
            while i < len(tool_calls) and tool_calls[i]["name"] in READ_ONLY_TOOLS:
                batch.append((i, tool_calls[i]))
                i += 1
            batch_results = await asyncio.gather(
                *(call_tool_fn(tc["name"], tc["args"], idx) for idx, tc in batch)
            )
            for (idx, _tc), r in zip(batch, batch_results):
                results[idx] = r
        else:
            results[i] = await call_tool_fn(tool_calls[i]["name"], tool_calls[i]["args"], i)
            i += 1
    return results
