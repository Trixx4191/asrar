"""Run a bounded, read-only skill/tool smoke test against a configured model.

Usage: venv/bin/python scripts/smoke.py --model groq-fast
Uses a temporary database and never exposes API keys or edits project files.
"""

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from core import agent, memory
from fastapi import FastAPI
import httpx
from api.routes import chat


async def smoke(model: str, timeout: float):
    original = agent._call_tool

    async def read_only(name, args, conversation_id=None):
        if name not in {"use_skill", "list_skills"}:
            return "Error: smoke test permits only skill discovery and loading."
        return await original(name, args, conversation_id=conversation_id)

    with tempfile.TemporaryDirectory(prefix="asrar-smoke-") as directory:
        with patch.object(memory, "DB_PATH", Path(directory) / "smoke.db"), \
             patch.object(agent, "LOG_PATH", Path(directory) / "actions.log"), \
             patch.object(agent, "_call_tool", new=read_only):
            memory.init_db()
            conversation_id = memory.create_conversation("Read-only smoke test")
            events = []

            async def collect():
                app = FastAPI()
                app.include_router(chat.router, prefix="/chat")
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                    response = await client.post("/chat/stream", json={
                        "message": "Call use_skill with name code-review. Then report the skill name "
                        "and its heading, and say SKILLS_OK. Do not inspect or edit project "
                        "files or call any other tool. This is a read-only integration test.",
                        "force_model": model, "conversation_id": conversation_id,
                    })
                for line in response.text.splitlines():
                    if line.startswith("data:"):
                        events.append(json.loads(line[5:]))

            try:
                await asyncio.wait_for(collect(), timeout=timeout)
            except TimeoutError:
                print(json.dumps({"success": False, "model": model, "error": "Smoke test timed out",
                                  "skill_loaded_by_tool": any(e.get("tool_result") == "use_skill" and e.get("success") for e in events),
                                  "last_event": next((e for e in reversed(events) if e.get("meta") or e.get("timing")), None)}))
                return False
            done = next((e for e in reversed(events) if e.get("done")), {})
            tools = done.get("tool_calls", [])
            loaded = any(c.get("tool") == "use_skill" and c.get("success")
                         and c.get("args", {}).get("name") == "code-review" for c in tools)
            ok = loaded and "SKILLS_OK" in done.get("response", "")
            persisted = memory.get_messages_full(conversation_id)
            saved = bool(persisted and persisted[-1]["role"] == "assistant"
                         and persisted[-1]["content"] == done.get("response"))
            ok = ok and saved
            error = next((e["error"] for e in events if "error" in e), None)
            print(json.dumps({
                "success": ok, "model": model, "skill_loaded_by_tool": loaded,
                "assistant_persisted": saved,
                "response": done.get("response"), "timings": done.get("timings"),
                "error": error,
            }, indent=2))
            return ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="groq-fast")
    parser.add_argument("--timeout", type=float, default=40)
    args = parser.parse_args()
    sys.exit(0 if asyncio.run(smoke(args.model, args.timeout)) else 1)
