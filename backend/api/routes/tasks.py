"""
Route: /tasks
Read and query the agent's action log.
"""

from fastapi import APIRouter
from pathlib import Path
import json

router = APIRouter()

LOG_PATH = Path(__file__).parent.parent.parent.parent / "logs" / "actions.log"


@router.get("")
async def get_tasks(limit: int = 50):
    """Return recent logged tasks."""
    if not LOG_PATH.exists():
        return {"tasks": []}

    lines = LOG_PATH.read_text(encoding="utf-8").strip().splitlines()
    tasks = []
    for line in reversed(lines[-limit:]):
        try:
            tasks.append(json.loads(line))
        except Exception:
            pass

    return {"tasks": tasks, "total": len(lines)}


@router.delete("")
async def clear_tasks():
    """Clear the action log."""
    if LOG_PATH.exists():
        LOG_PATH.write_text("")
    return {"success": True}
