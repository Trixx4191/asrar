"""
Plan Manager — checkpoints + rollback semantics for multi-step work.

Usage pattern:
  1. Agent calls update_plan → plan_manager also saves a checkpoint labeled "before:<step>"
  2. On tool failure of a mutating tool, orchestrator can call rollback_last_checkpoint
  3. UI can list checkpoints via memory.list_plan_checkpoints
"""

from __future__ import annotations

from core import memory


def checkpoint_before_step(conversation_id: str, step_label: str | None = None) -> int:
    label = f"before:{step_label}" if step_label else "auto"
    ckpt_id = memory.save_plan_checkpoint(conversation_id, label=label)
    memory.clear_old_checkpoints(conversation_id, keep=8)
    return ckpt_id


def rollback_to_checkpoint(conversation_id: str, checkpoint_id: int) -> dict:
    ok = memory.restore_plan_checkpoint(conversation_id, checkpoint_id)
    if not ok:
        return {"success": False, "error": f"Checkpoint {checkpoint_id} not found"}
    items = memory.get_plan(conversation_id)
    return {"success": True, "restored_items": items, "checkpoint_id": checkpoint_id}


def rollback_last(conversation_id: str) -> dict:
    ckpts = memory.list_plan_checkpoints(conversation_id, limit=1)
    if not ckpts:
        return {"success": False, "error": "No checkpoints available"}
    return rollback_to_checkpoint(conversation_id, ckpts[0]["id"])


def mark_step_failed(conversation_id: str, step_id: str | None = None) -> list[dict]:
    """Mark the in_progress step as failed (pending) so the plan stays honest."""
    items = memory.get_plan(conversation_id)
    changed = False
    for it in items:
        if it.get("status") == "in_progress":
            it["status"] = "pending"
            it["note"] = (it.get("note") or "") + " [rolled back after failure]"
            changed = True
            break
        if step_id and str(it.get("id")) == str(step_id):
            it["status"] = "pending"
            it["note"] = (it.get("note") or "") + " [failed]"
            changed = True
            break
    if changed:
        memory.set_plan(conversation_id, items)
    return items
