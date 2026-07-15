"""
Orchestrator scaffold

This module provides a single `Orchestrator` class that will be the
high-level composition point for routing, supervision, tool execution,
and provider adapter logic. For now it wraps the existing
`agent.run_task()` function for backward compatibility. During the
refactor we'll incrementally move responsibilities into this class.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Import the existing agent loop as a compatibility shim
from core import agent
from providers.base import Message


class Orchestrator:
    """High-level orchestrator for agent workflow.

    Current behavior: delegating to `agent.run_task` to preserve
    existing functionality while providing a clear place to add
    richer orchestration, concurrency, and observability.
    """

    def __init__(self, *, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    async def run(
        self,
        user_input: str,
        history: Optional[List[Message]] = None,
        force_model: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute a single user task and return a structured result.

        Eventually this will: classify, route, run model calls, run tools
        possibly concurrently, update plans, and return a rich trace.
        For now it calls into the existing agent implementation.
        """
        return await agent.run_task(
            user_input,
            history=history,
            force_model=force_model,
            conversation_id=conversation_id,
        )


# Convenience module-level instance for quick imports
_default_orchestrator = Orchestrator()


async def run(user_input: str, history=None, force_model=None, conversation_id=None):
    return await _default_orchestrator.run(user_input, history=history, force_model=force_model, conversation_id=conversation_id)
