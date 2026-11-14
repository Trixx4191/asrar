"""Shared request shaping and usage accounting for buffered and streaming tasks."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import json
import math
import time

from core import context as context_mgr
from core.tool_catalog import ToolCatalog


def payload_chars(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


@dataclass
class TaskRuntime:
    catalog: ToolCatalog
    model_requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reported_usage_requests: int = 0
    estimated_input_tokens: int = 0
    context_chars_saved: int = 0
    tool_schema_chars: int = 0
    model_ms: float = 0.0
    started_at: float = field(default_factory=time.perf_counter)

    def prepare(self, messages: list[dict], system: str) -> tuple[list[dict], list[dict]]:
        prepared = context_mgr.prepare_messages(messages)
        schemas = self.catalog.schemas
        old_size = payload_chars(messages)
        new_size = payload_chars(prepared)
        schema_size = payload_chars(schemas)
        self.model_requests += 1
        self.context_chars_saved += max(0, old_size - new_size)
        self.tool_schema_chars += schema_size
        self.estimated_input_tokens += math.ceil((new_size + schema_size + len(system)) / 4)
        return prepared, schemas

    def observe(self, response) -> None:
        incoming = int(getattr(response, "input_tokens", 0) or 0)
        outgoing = int(getattr(response, "output_tokens", 0) or 0)
        self.input_tokens += incoming
        self.output_tokens += outgoing
        if incoming or outgoing:
            self.reported_usage_requests += 1

    def summary(self) -> dict:
        return {
            "model_requests": self.model_requests,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reported_usage_requests": self.reported_usage_requests,
            "estimated_input_tokens": self.estimated_input_tokens,
            "estimate_method": "request characters / 4; not billable-token accounting",
            "context_chars_saved": self.context_chars_saved,
            "tool_schema_chars": self.tool_schema_chars,
            "active_tools": len(self.catalog.schemas),
            "model_ms": round(self.model_ms, 2),
            "total_ms": round((time.perf_counter() - self.started_at) * 1000, 2),
        }


_current: ContextVar[TaskRuntime | None] = ContextVar("asrar_task_runtime", default=None)


def current() -> TaskRuntime | None:
    return _current.get()


@contextmanager
def task_scope(schemas):
    state = TaskRuntime(ToolCatalog(schemas))
    token = _current.set(state)
    try:
        yield state
    finally:
        _current.reset(token)
