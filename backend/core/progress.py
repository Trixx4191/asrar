"""Per-task detection of repeated tool work without new observations."""

from __future__ import annotations

import hashlib
import json


STALLED_TOOL_RESPONSE = (
    "Stopped because the same tools returned identical results for three consecutive rounds. "
    "This task is incomplete; repeated work was stopped to avoid wasting time and compute."
)


class ToolProgressGuard:
    """Stop after three identical consecutive rounds of inputs and results.

    Tool ids and assistant narration change even when a model repeats the
    same work. Compare actual inputs and full results instead. Changed
    arguments, an intervening edit, or a changing poll result resets the
    count. Keep one instance per task attempt, never per conversation.
    """

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        """Forget prior observations after externally detected progress."""
        self._last_fingerprint = None
        self._repeats = 0

    def stalled(self, tool_calls: list[dict], results: list[str]) -> bool:
        observed = [(call["name"], call["args"], result)
                    for call, result in zip(tool_calls, results)]
        fingerprint = hashlib.sha256(
            json.dumps(observed, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).digest()
        self._repeats = self._repeats + 1 if fingerprint == self._last_fingerprint else 1
        self._last_fingerprint = fingerprint
        return self._repeats >= 3

    def observe(self, name: str, args: dict, result: str) -> bool:
        """Observe one native tool result with the same semantics as a round."""
        return self.stalled([{"name": name, "args": args}], [result])
