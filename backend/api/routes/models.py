"""
Route: /models
List, add, remove, toggle models in the live registry.
Also handles the "add by name or URL" agent lookup flow.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from core.registry import (
    list_models, add_model, remove_model,
    toggle_model, lookup_and_register_model, configure_model, test_model as probe_model,
    PROVIDER_DEFAULTS,
)

router = APIRouter()


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AddModelRequest(ModelRequest):
    key: str
    model_id: str
    provider: str
    display_name: str
    strengths: list[str]
    context_window: int = 32000
    api_base: str | None = None
    free: bool = False
    priority: int = 2
    protocol: str | None = None
    api_key_env: str | None = None
    validate_connection: bool = Field(default=True, alias="validate")


class LookupRequest(ModelRequest):
    name_or_url: str  # e.g. "qwen/qwen-2.5-72b" or "https://openrouter.ai/models/..."
    provider: str | None = None
    key: str | None = None
    api_base: str | None = None
    protocol: str | None = None
    api_key_env: str | None = None
    strengths: list[str] | None = None
    priority: int = 2
    validate_connection: bool = Field(default=True, alias="validate")


class UpdateModelRequest(ModelRequest):
    model_id: str | None = None
    provider: str | None = None
    display_name: str | None = None
    strengths: list[str] | None = None
    context_window: int | None = None
    api_base: str | None = None
    protocol: str | None = None
    api_key_env: str | None = None
    priority: int | None = None
    free: bool | None = None


class ToggleRequest(BaseModel):
    enabled: bool


@router.get("")
async def get_models(only_enabled: bool = False):
    return {"models": list_models(only_enabled=only_enabled)}


@router.post("")
async def add_model_manual(req: AddModelRequest):
    try:
        args = req.model_dump(by_alias=True)
        validate = args.pop("validate")
        entry = add_model(**args)
        if validate:
            entry["validation"] = await probe_model(entry["key"])
        return {"success": True, "model": entry}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/lookup")
async def lookup_model(req: LookupRequest):
    """Agent looks up model by name/URL and auto-registers it."""
    try:
        entry = await lookup_and_register_model(**req.model_dump(by_alias=True))
        return {"success": True, "model": entry}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/providers")
async def providers():
    return {"providers": PROVIDER_DEFAULTS, "protocols": ["openai_compatible", "anthropic", "google"]}


@router.patch("/{key}")
async def update_model(key: str, req: UpdateModelRequest):
    try:
        entry = configure_model(key, **req.model_dump(exclude_unset=True, exclude_none=True))
        return {"success": True, "model": entry}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/{key}/test")
async def test_connection(key: str):
    try:
        return {"validation": await probe_model(key)}
    except ValueError as exc:
        raise HTTPException(404, str(exc))


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
