"""
Asrār — Supervisor
backend/core/supervisor.py

A thin orchestration layer above core.router.route(). It exists to fix a
specific bug: the classifier treats every incoming message as an independent
task, with no memory of "I just asked this conversation a clarifying
question." That causes mid-conversation misrouting — e.g. one model asks
"what stack do you want?", the user answers "Python", and the classifier
sends that one-word answer off to a completely different model that has no
idea a question was ever asked.

What this module does:
  - Sticky routing: if the conversation has an open clarifying question
    (the last assistant turn made no tool calls and asked something), the
    next user message stays with the same model instead of being
    reclassified from scratch.
  - Execution log: every routing decision gets a row in execution_log
    (core.memory) — sticky vs fresh, which model, why — so a conversation's
    behavior is inspectable after the fact instead of being a black box.

What this does NOT do (yet): cross-turn slot-filling/checklists, parallel
model dispatch, or a judge step that reviews tool output before accepting
it. Those are real extensions of "supervision" but a separate, bigger build
than the bug this fixes.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import memory
from core.router import route, load_registry, RoutingDecision
from providers import get_provider


def _is_clarifying_question(text: str, tool_calls: list | None) -> bool:
    """Heuristic for 'this turn ended with an open question, not a finished task.'

    No tool calls were made AND the response contains a question mark. This
    is deliberately simple — it can have false positives (a rhetorical
    question in an otherwise-complete answer) and false negatives (a
    clarifying request phrased as a statement, e.g. "Let me know the stack.")
    It directly targets the failure mode that was observed: a clarifying
    question being treated as a finished, independently-routable turn.
    """
    if tool_calls:
        return False
    if not text or "?" not in text:
        return False
    return True


def decide_route(conversation_id: str, user_input: str, force_model: str | None = None) -> RoutingDecision:
    """Routing decision, aware of any open clarifying question in this conversation.

    - Explicit force_model always wins (the user picked a model on purpose).
    - If the conversation is mid-clarification, stay with the model that
      asked, as long as it's still available.
    - Otherwise, route normally (fresh classification).
    """
    if force_model:
        decision = route(user_input, force_model=force_model)
        memory.log_event(conversation_id, "manual_override", {"model": force_model})
        return decision

    state = memory.get_state(conversation_id)

    if state and state.get("awaiting_clarification"):
        pending_key = state.get("pending_model_key")
        registry = load_registry()
        pending_model = registry["models"].get(pending_key)

        if pending_model:
            try:
                pending_available = get_provider(pending_model["provider"]).is_available()
            except Exception:
                pending_available = False

            if pending_available:
                memory.log_event(
                    conversation_id, "sticky_route",
                    {"reason": "open clarifying question, continuing same model"},
                    model=pending_model["display_name"],
                )
                return RoutingDecision(
                    task_type=state.get("task_type") or "general",
                    selected_model_key=pending_key,
                    selected_model=pending_model,
                    fallback_chain=[],
                    sticky=True,
                    reason=(
                        f"Staying with '{pending_model['display_name']}' — it asked a "
                        f"clarifying question last turn that hasn't been answered yet."
                    ),
                )

        memory.log_event(
            conversation_id, "sticky_route_unavailable",
            {"reason": "pending model no longer available, falling back to fresh routing"},
        )

    decision = route(user_input, force_model=None)
    memory.log_event(
        conversation_id, "fresh_route",
        {"task_type": decision.task_type, "reason": decision.reason},
        model=decision.selected_model["display_name"],
    )
    return decision


def after_response(
    conversation_id: str,
    model_key: str,
    model: dict,
    task_type: str | None,
    text: str,
    tool_calls: list | None,
) -> None:
    """Call once a model's turn is fully done. Decides whether the next
    message should stay sticky to this model (open clarifying question) or
    reset to normal per-message routing (the task moved forward)."""

    if _is_clarifying_question(text, tool_calls):
        memory.set_awaiting(
            conversation_id,
            model_key=model_key,
            provider=model.get("provider", ""),
            display_name=model.get("display_name", model_key),
            task_type=task_type,
        )
        memory.log_event(
            conversation_id, "clarification_detected",
            {"note": "next message will stay with this model"},
            model=model.get("display_name", model_key),
        )
    else:
        memory.clear_awaiting(conversation_id)
        memory.log_event(
            conversation_id, "task_progressed",
            {"tool_calls": len(tool_calls or [])},
            model=model.get("display_name", model_key),
        )
