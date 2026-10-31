"""Shared guidance for doing useful work with fewer model/tool round trips."""

EFFICIENCY_INSTRUCTIONS = (
    "Work directly toward the requested outcome. Use a short plan only when the task has "
    "three or more distinct steps. Batch independent reads and searches; run dependent "
    "actions in order. Reuse information already available in this task. Repeat a tool "
    "call only when its inputs or relevant state changed, or when checking a running job. "
    "If an approach produces the same result without progress, change the approach. "
    "Run focused checks appropriate to the change, and rerun passing checks only after "
    "new changes or evidence justifies it. Continue until the requested work is complete; "
    "if blocked, explain the specific blocker and unfinished work.\n"
)
