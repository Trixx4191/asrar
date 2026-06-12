"""
Route: /models
List, add, remove, toggle models in the live registry.
Also handles the "add by name or URL" agent lookup flow.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from core.registry import (
    list_models, add_model, remove_model,
    toggle_model, lookup_and_register_model
)

router = APIRouter()


class AddModelRequest(BaseModel):
    key: str
    model_id: str
    provider: str
    display_name: str
    strengths: list[str]
    context_window: int = 32000
    api_base: str | None = None
    free: bool = True
    priority: int = 2


class LookupRequest(BaseModel):
    name_or_url: str  # e.g. "qwen/qwen-2.5-72b" or "https://openrouter.ai/models/..."


class ToggleRequest(BaseModel):
    enabled: bool


@router.get("")
async def get_models(only_enabled: bool = False):
    return {"models": list_models(only_enabled=only_enabled)}


@router.post("")
async def add_model_manual(req: AddModelRequest):
    try:
        entry = add_model(**req.model_dump())
        return {"success": True, "model": entry}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/lookup")
async def lookup_model(req: LookupRequest):
    """Agent looks up model by name/URL and auto-registers it."""
    entry = await lookup_and_register_model(req.name_or_url)
    if entry:
        return {"success": True, "model": entry}
    raise HTTPException(400, "Could not find or register model.")


@router.delete("/{key}")
async def delete_model(key: str):
    ok = remove_model(key)
    if not ok:
        raise HTTPException(404, f"Model '{key}' not found.")
    return {"success": True}


@router.patch("/{key}/toggle")
async def toggle(key: str, req: ToggleRequest):
    ok = toggle_model(key, req.enabled)
    if not ok:
        raise HTTPException(404, f"Model '{key}' not found.")
    return {"success": True, "enabled": req.enabled}
