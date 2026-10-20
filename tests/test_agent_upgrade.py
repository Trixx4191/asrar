"""Behavioral regressions for skill discovery, routing latency, and tool turns."""

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core import agent, classifier, hooks, memory, orchestrator, skills
from core.router import route_async
from providers.base import Message, ProviderCapabilities, ProviderResponse
from providers.anthropic import AnthropicProvider
from providers.google import GoogleProvider
from providers.transport import provider_error


class FastRoutingTest(unittest.IsolatedAsyncioTestCase):
    async def test_default_route_does_not_make_classifier_request(self):
        with patch.dict(os.environ, {"ASRAR_ROUTING_MODE": "fast"}), \
             patch.object(classifier, "_model_classify", new_callable=AsyncMock) as model:
            result = await classifier.classify_task_async("hello", history=[
                {"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}])
            self.assertEqual(result.task_type, "fast_chat")
            model.assert_not_awaited()

    async def test_hybrid_classification_times_out_and_uses_local_result(self):
        async def slow(*args, **kwargs):
            await asyncio.sleep(10)
        with patch.dict(os.environ, {"ASRAR_ROUTING_MODE": "hybrid", "ASRAR_ROUTING_TIMEOUT": "0.05"}), \
             patch.object(classifier, "_model_classify", new=slow):
            start = time.perf_counter()
            result = await classifier.classify_task_async("something ambiguous")
            self.assertEqual(result.source, "keyword")
            self.assertLess(time.perf_counter() - start, 0.5)

    async def test_manual_selection_bypasses_async_classifier(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "test"}), \
             patch("core.router.classify_task_async", new_callable=AsyncMock) as model:
            result = await route_async("hello", force_model="llama-70b")
            self.assertEqual(result.selected_model_key, "llama-70b")
            model.assert_not_awaited()

    async def test_invalid_manual_selection_is_explicit(self):
        with self.assertRaisesRegex(RuntimeError, "Selected model"):
            await route_async("hello", force_model="does-not-exist")

    async def test_short_followup_retains_task_context(self):
        result = await classifier.classify_task_async("yes please", history=[
            {"role": "user", "content": "debug this python function"},
            {"role": "assistant", "content": "Need a test?"}])
        self.assertEqual(result.task_type, "coding")

    async def test_keywords_do_not_match_inside_words(self):
        self.assertNotIn("hi", classifier.classify_task("this thing").keywords_matched)


class SkillContextTest(unittest.TestCase):
    def test_descriptions_are_available_without_loading_all_bodies(self):
        context, loaded = skills.prompt_context("hello")
        self.assertIn("code-review:", context)
        self.assertNotIn("# Code Review", context)
        self.assertEqual(loaded, [])

    def test_explicit_invocation_loads_and_deduplicates_requested_body(self):
        context, loaded = skills.prompt_context("Use $code-review and $code-review")
        self.assertEqual(loaded, ["code-review"])
        self.assertEqual(context.count("# Code Review"), 1)

    def test_missing_frontmatter_end_is_rejected(self):
        self.assertEqual(skills._frontmatter("---\nname: demo\ndescription: incomplete"), {})

    def test_external_symlink_is_not_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "skills/demo").mkdir(parents=True)
            target = root / "outside.md"
            target.write_text("---\nname: demo\ndescription: external\n---\nbody")
            (root / "skills/demo/SKILL.md").symlink_to(target)
            with patch.object(skills, "SKILLS_ROOT", root / "skills"):
                self.assertIsNone(skills.load_skill("demo"))


class IsolatedMemoryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = patch.object(memory, "DB_PATH", Path(self.tmp.name) / "test.db")
        self.log = patch.object(agent, "LOG_PATH", Path(self.tmp.name) / "actions.log")
        self.db.start()
        self.log.start()
        memory.init_db()
        self.cid = memory.create_conversation("test")

    def tearDown(self):
        self.db.stop()
        self.log.stop()
        self.tmp.cleanup()

    async def test_actual_skill_tool_load_is_recorded(self):
        result = await agent._call_tool("use_skill", {"name": "code-review"}, self.cid)
        self.assertIn("# Code Review", result)
        self.assertTrue(any(row["event"] == "skill_loaded" for row in memory.get_log(self.cid)))

    async def test_failed_verification_leaves_changes_outstanding(self):
        memory.mark_dirty(self.cid, "main.py")
        agent._track_verification_state("run_tests", {}, "❌ tests failed", self.cid)
        self.assertTrue(memory.get_verification_state(self.cid)["dirty"])

    async def test_trusted_approval_is_scoped_to_exact_action(self):
        args = {"package": "example"}
        with hooks.approved_action("pip_install", args):
            self.assertFalse(hooks.run_pre_tool_hooks("pip_install", args, self.cid).blocked)
            self.assertTrue(hooks.run_pre_tool_hooks("pip_install", {"package": "other"}, self.cid).blocked)
        self.assertTrue(hooks.run_pre_tool_hooks("pip_install", args, self.cid).blocked)

    async def test_streaming_claude_keeps_tool_blocks_on_second_call(self):
        calls = []

        class Provider:
            capabilities = ProviderCapabilities()
            def is_available(self):
                return True
            async def stream_complete(self, **kwargs):
                calls.append(kwargs)
                response = ProviderResponse(content="", model_id="test", provider="anthropic")
                if len(calls) == 1:
                    response.tool_calls = [{"id": "call-1", "name": "use_skill", "args": {"name": "code-review"}}]
                else:
                    yield {"type": "token", "text": "SKILLS_OK"}
                    response.content = "SKILLS_OK"
                yield {"type": "done", "response": response}

        orchestrator.reset_circuit("anthropic")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}), \
             patch.object(agent, "get_provider", return_value=Provider()):
            events = [e async for e in agent.run_task_stream("$code-review", force_model="claude-haiku", conversation_id=self.cid)]
        self.assertEqual(len(calls), 2)
        self.assertIn("# Code Review", calls[0]["system"])
        converted = AnthropicProvider()._to_messages(calls[1]["messages"])
        self.assertEqual(converted[-2]["content"][0]["type"], "tool_use")
        self.assertEqual(converted[-1]["content"][0]["tool_use_id"], "call-1")
        self.assertEqual(events[-1]["response"], "SKILLS_OK")
        self.assertIn("routing_ms", events[-1]["timings"])


class ProviderProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def test_google_preserves_function_call_before_response(self):
        provider = GoogleProvider()
        contents = provider._build_contents([
            Message("assistant", "", tool_calls=[{"function": {"name": "use_skill", "arguments": '{"name":"commit"}'}}]),
            Message("tool", "loaded", name="use_skill"),
        ], None)
        self.assertEqual(contents[0]["parts"][0]["functionCall"]["args"], {"name": "commit"})
        self.assertEqual(contents[1]["parts"][0]["functionResponse"]["name"], "use_skill")

    async def test_buffered_completion_uses_provider_adapter(self):
        provider = AsyncMock()
        provider.complete.return_value = ProviderResponse("done", "test", "google")
        result = await agent._llm_call_with_tools(provider, "test", [{"role": "user", "content": "hi"}], "system", "google")
        self.assertEqual(result, ("done", []))
        self.assertEqual(provider.complete.call_args.kwargs["messages"][0].content, "hi")

    async def test_transient_stream_error_retries_before_output(self):
        attempts = []
        async def stream(**kwargs):
            attempts.append(1)
            response = ProviderResponse("", "test", "test", success=False, error="Provider HTTP 429")
            if len(attempts) > 1:
                response.success = True
                yield {"type": "token", "text": "ok"}
            yield {"type": "done", "response": response}
        with patch.object(orchestrator, "retry_delay_seconds", return_value=0):
            events = [event async for event in orchestrator.stream_with_retry(stream)]
        self.assertEqual(len(attempts), 2)
        self.assertEqual(events[0]["text"], "ok")

    async def test_stream_does_not_replay_after_output(self):
        attempts = []
        async def stream(**kwargs):
            attempts.append(1)
            yield {"type": "token", "text": "partial"}
            yield {"type": "done", "response": ProviderResponse("", "test", "test", success=False, error="Provider HTTP 503")}
        events = [event async for event in orchestrator.stream_with_retry(stream)]
        self.assertEqual(len(attempts), 1)
        self.assertFalse(events[-1]["response"].success)

    async def test_errors_do_not_expose_key_bearing_urls(self):
        error = httpx.ConnectError("https://example.test?key=SECRET")
        self.assertNotIn("SECRET", provider_error(error))


if __name__ == "__main__":
    unittest.main()
