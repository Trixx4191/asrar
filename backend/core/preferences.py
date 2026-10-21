"""Validated, persistent desktop preferences. No credentials belong here."""
import json
import os
import tempfile
from pathlib import Path
from threading import RLock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ROOT = Path(__file__).resolve().parents[2]
PREFERENCES_PATH = ROOT / "data" / "preferences.json"
_lock = RLock()


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_name: str = Field(default="Asrār", min_length=1, max_length=40)
    runtime: Literal["codex", "claude", "kimi", "muse", "api"] = "codex"
    workspace: str = str(ROOT)
    codex_model: str | None = Field(default=None, max_length=160)
    reasoning_effort: str | None = Field(default=None, max_length=30)
    claude_model: str | None = Field(default=None, max_length=160)
    claude_effort: str | None = Field(default=None, max_length=30)
    kimi_model: str | None = Field(default=None, max_length=160)
    muse_model: str | None = Field(default=None, max_length=160)
    muse_effort: str | None = Field(default=None, max_length=30)
    access_mode: Literal["read-only", "workspace-write"] = "read-only"
    network_access: bool = False
    web_search: Literal["disabled", "cached", "live"] = "disabled"
    instructions: str = Field(default="", max_length=12000)
    show_activity: bool = True
    enter_to_send: bool = True
    theme: Literal["dark", "light", "system"] = "dark"

    @field_validator("workspace")
    @classmethod
    def valid_workspace(cls, value):
        path = Path(value).expanduser().resolve()
        if not path.is_dir():
            raise ValueError("Choose an existing workspace folder")
        return str(path)


def load_preferences() -> Preferences:
    with _lock:
        try:
            return Preferences.model_validate_json(PREFERENCES_PATH.read_text())
        except FileNotFoundError:
            return Preferences()


def save_preferences(changes: dict) -> Preferences:
    with _lock:
        settings = Preferences.model_validate({**load_preferences().model_dump(), **changes})
        PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=PREFERENCES_PATH.parent, delete=False) as handle:
            temporary = Path(handle.name)
            try:
                json.dump(settings.model_dump(), handle, indent=2)
                handle.write("\n")
                handle.close()
                os.replace(temporary, PREFERENCES_PATH)
            finally:
                temporary.unlink(missing_ok=True)
        return settings
