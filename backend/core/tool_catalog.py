"""Task-sized native tool menus with explicit discovery for every capability."""
from __future__ import annotations

GROUPS = {
    "files": {"read_file", "list_dir", "write_file", "edit_file", "create_project"},
    "code": {"search_code", "find_files", "run_command", "run_tests", "execute_code", "semantic_search"},
    "web": {"web_search", "fetch_page", "download_url"},
    "git": {"git_status", "git_diff", "git_log", "git_commit"},
    "system": {"diagnose_system", "list_processes", "inspect_process", "kill_process", "run_command"},
    "packages": {"pip_install", "npm_install"},
    "models": {"list_models", "register_model", "configure_model", "test_model"},
    "skills": {"list_skills", "use_skill"},
    "planning": {"update_plan", "rollback_plan"},
}
DISCOVERY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "discover_tools",
        "description": "Enable additional native tools for this task. Choose a category when the current menu lacks a capability; all exposes the complete catalog. Discovery does not execute actions or grant permission.",
        "parameters": {
            "type": "object",
            "properties": {"category": {"type": "string", "enum": [*GROUPS, "all"]}},
            "required": ["category"],
        },
    },
}

PROFILES = {
    "coding": {"files", "code", "planning"},
    "document_work": {"files", "planning"},
    "web_research": {"web"},
    "pc_diagnosis": {"system", "code"},
    "shell_automation": {"code", "packages", "planning"},
    "analysis": {"files", "code"},
    "summarization": set(),
    "fast_chat": set(),
    "deep_reasoning": set(),
    "multimodal": {"files"},
    "general": {"files", "code", "planning"},
}


class ToolCatalog:
    """A per-task menu, never a global mutable tool list or security boundary."""

    def __init__(self, schemas: list[dict]):
        self._schemas = schemas
        self._by_name = {item["function"]["name"]: item for item in schemas}
        self.enabled = {"discover_tools", "use_skill"}

    def configure(self, task_type: str) -> None:
        for category in PROFILES.get(task_type, PROFILES["general"]):
            self.enabled.update(GROUPS[category])

    @property
    def schemas(self) -> list[dict]:
        # Retain catalog order so unchanged menus have a stable cache prefix.
        return [schema for schema in self._schemas if schema["function"]["name"] in self.enabled]

    def discover(self, category: str) -> str:
        if category != "all" and category not in GROUPS:
            return "Error: unknown tool category. Choose: " + ", ".join([*GROUPS, "all"])
        names = set(self._by_name) if category == "all" else GROUPS[category]
        self.enabled.update(names)
        lines = ["These tools are now available for native calls:"]
        for schema in self.schemas:
            fn = schema["function"]
            if fn["name"] in names:
                lines.append(f"- {fn['name']}: {fn['description'].splitlines()[0][:120]}")
        return "\n".join(lines)
