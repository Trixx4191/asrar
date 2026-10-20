"""Live chat-driven model registration in an isolated registry and database.

Copies credentials into a private temporary .env, never prints their values,
and permits only registration of one specified Groq model. Makes real requests.
"""

import argparse
import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core import agent, memory, registry, router
from providers import configuration


async def smoke(model: str):
    original_tool = agent._call_tool
    original_config = registry.CONFIG_PATH.read_text()
    env_contents = configuration.ENV_PATH.read_text() if configuration.ENV_PATH.exists() else ""
    calls = []

    async def restricted(name, args, conversation_id=None):
        if name == "register_model" and (args.get("name_or_url") != "openai/gpt-oss-20b"
                or args.get("provider") != "groq" or args.get("key") != "registration-smoke"):
            return "Error: use the exact provider, model ID, and key requested in this smoke test"
        if name not in {"register_model", "list_models", "test_model"}:
            return "Error: only model registration and validation tools are permitted in this smoke test"
        if name == "test_model" and args.get("key") != "registration-smoke":
            return "Error: only the temporary registered model may be tested"
        calls.append(name)
        return await original_tool(name, args, conversation_id=conversation_id)

    with ExitStack() as stack:
        directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="asrar-model-smoke-")))
        config_path = directory / "models.json"
        config_path.write_text(original_config)
        env_path = directory / ".env"
        with os.fdopen(os.open(env_path, os.O_WRONLY | os.O_CREAT, 0o600), "w") as handle:
            handle.write(env_contents)
        for owner, attribute, value in (
            (registry, "CONFIG_PATH", config_path), (router, "CONFIG_PATH", config_path),
            (configuration, "ENV_PATH", env_path), (memory, "DB_PATH", directory / "smoke.db"),
            (agent, "LOG_PATH", directory / "actions.log"), (agent, "_call_tool", restricted),
        ):
            stack.enter_context(patch.object(owner, attribute, value))
        memory.init_db()
        cid = memory.create_conversation("Isolated model registration smoke test")

        async def run():
            return [event async for event in agent.run_task_stream(
                "Add model openai/gpt-oss-20b using native provider groq, registry key "
                "registration-smoke, and strengths fast_chat. Use register_model and test "
                "the connection. Do not edit source files or use shell/file tools. "
                "If registration and its validation succeed, reply MODEL_REGISTRATION_OK.",
                force_model=model, conversation_id=cid,
            )]

        try:
            events = await asyncio.wait_for(run(), timeout=40)
        except TimeoutError:
            print(json.dumps({"success": False, "error": "Registration smoke test timed out"}))
            return False
        entry = registry.load_registry()["models"].get("registration-smoke", {})
        done = next((event for event in reversed(events) if event.get("done")), {})
        ok = ("register_model" in calls and entry.get("validation", {}).get("ready")
              and "MODEL_REGISTRATION_OK" in done.get("response", ""))
        print(json.dumps({
            "success": bool(ok), "agent_tools": calls, "registered_model": entry.get("id"),
            "provider": entry.get("provider"), "validation": entry.get("validation"),
            "response": done.get("response"), "timings": done.get("timings"),
            "error": next((event.get("error") for event in events if event.get("error")), None),
        }, indent=2))
        return bool(ok)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="groq-code")
    args = parser.parse_args()
    sys.exit(0 if asyncio.run(smoke(args.model)) else 1)
