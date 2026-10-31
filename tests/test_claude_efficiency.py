"""Repeated inspections stop actual SDK work and remain visibly incomplete."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core import claude_chat, memory
from core.claude_bridge import ClaudeBridge
from core.execution_policy import EFFICIENCY_INSTRUCTIONS
from fixtures.claude_sdk import SDK, Client, AssistantMessage, ToolUseBlock, UserMessage, ToolResultBlock, ResultMessage


class InspectionClient(Client):
    async def receive_response(self):
        for index, observation in enumerate(json.loads(self.message)):
            grouped = isinstance(observation, dict)
            rows = observation["batch"] if grouped else [observation]
            uses, results = [], []
            for offset, (name, result) in enumerate(rows):
                identifier = f"call-{index}-{offset}"
                uses.append(ToolUseBlock(identifier, name, {"pattern": f"target-{offset}"}))
                results.append(ToolResultBlock(identifier, result))
            yield AssistantMessage(uses)
            if grouped and observation.get("split_results"):
                for result in reversed(results):
                    yield UserMessage([result])
            else:
                yield UserMessage(results)
        yield ResultMessage(result="Work completed.")


class ClaudeExecutionEfficiencyTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = ClaudeBridge()
        self.account = {"type": "claude.ai", "email": "test@example.invalid", "plan": "pro"}
        self.bridge.require_account = AsyncMock(return_value=self.account)
        self.bridge.models = AsyncMock(return_value=[])
        self.bridge.scope = lambda account: "test-scope"
        self.settings = SimpleNamespace(
            workspace=self.temp.name, access_mode="read-only", web_search="disabled",
            network_access=False, claude_model=None, claude_effort=None,
            instructions="Keep the user's instructions last.",
        )
        sdk = SimpleNamespace(**{name: getattr(SDK, name) for name in dir(SDK) if not name.startswith("_")})
        sdk.ClaudeSDKClient = InspectionClient
        self.patches = [
            patch.object(memory, "DB_PATH", Path(self.temp.name) / "test.db"),
            patch.object(claude_chat, "bridge", self.bridge),
            patch.object(claude_chat, "load_preferences", return_value=self.settings),
            patch.object(claude_chat, "sdk_types", return_value=sdk),
            patch.object(claude_chat, "executable", return_value="fake-claude"),
            patch.object(claude_chat, "sdk_environment", return_value={}),
        ]
        for patcher in self.patches:
            patcher.start()
        memory.init_db()
        self.conversation = memory.create_conversation()
        SDK.clients = []

    async def asyncTearDown(self):
        await self.bridge.close()
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temp.cleanup()

    async def collect(self, observations):
        return [event async for event in claude_chat.stream_chat(json.dumps(observations), self.conversation)]

    async def test_repeated_inspection_interrupts_runtime_and_persists_incomplete(self):
        events = await self.collect([("Grep", "same result")] * 8)
        done = next(event for event in events if event.get("done"))
        self.assertTrue(done["incomplete"])
        self.assertEqual(done["stop_reason"], "repeated_tool_results")
        self.assertEqual(len(done["tool_calls"]), 3)
        self.assertIn("incomplete", done["response"])
        self.assertEqual(memory.get_messages_full(self.conversation)[-1]["content"], done["response"])
        self.assertTrue(SDK.clients[-1].interrupted)
        self.assertTrue(SDK.clients[-1].closed)
        self.assertFalse(self.bridge.active)
        self.bridge.require_account.assert_awaited_once()
        self.bridge.models.assert_awaited_once_with(account=self.account)

    async def test_changing_results_keep_running(self):
        events = await self.collect([("Grep", f"result {index}") for index in range(8)])
        done = next(event for event in events if event.get("done"))
        self.assertEqual(done["response"], "Work completed.")
        self.assertFalse(done.get("incomplete", False))
        self.assertEqual(len(done["tool_calls"]), 8)

    async def test_repeated_batches_stop_even_when_results_arrive_separately(self):
        observations = [{"batch": [("Grep", "same"), ("Read", "same file")], "split_results": True}] * 8
        events = await self.collect(observations)
        done = next(event for event in events if event.get("done"))
        self.assertTrue(done["incomplete"])
        self.assertEqual(len(done["tool_calls"]), 6)
        self.assertTrue(SDK.clients[-1].interrupted)

    async def test_interrupt_failure_still_closes_runtime_and_reports_incomplete(self):
        with patch.object(InspectionClient, "interrupt", new=AsyncMock(side_effect=RuntimeError("disconnected"))):
            events = await self.collect([("Grep", "same result")] * 8)
        done = next(event for event in events if event.get("done"))
        self.assertTrue(done["incomplete"])
        self.assertTrue(SDK.clients[-1].closed)
        self.assertFalse(self.bridge.active)

    async def test_intervening_tool_resets_inspection_guard(self):
        observations = [("Grep", "same")] * 2 + [("TodoWrite", "plan updated")] + [("Grep", "same")] * 2
        events = await self.collect(observations)
        done = next(event for event in events if event.get("done"))
        self.assertFalse(done.get("incomplete", False))
        self.assertEqual(done["response"], "Work completed.")

    async def test_large_results_compare_full_content(self):
        prefix = "x" * 30001
        events = await self.collect([("Grep", prefix + str(index)) for index in range(4)])
        done = next(event for event in events if event.get("done"))
        self.assertFalse(done.get("incomplete", False))

    async def test_efficiency_guidance_preserves_custom_instructions(self):
        await self.collect([])
        prompt = SDK.clients[-1].options.system_prompt["append"]
        self.assertIn(EFFICIENCY_INSTRUCTIONS, prompt)
        self.assertTrue(prompt.endswith(self.settings.instructions))


if __name__ == "__main__":
    unittest.main()
