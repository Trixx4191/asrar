"""Routing and speculative calls must make bounded, useful progress."""

import asyncio
import os
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core import classifier, orchestrator


class RoutingEfficiencyTest(unittest.IsolatedAsyncioTestCase):
    async def test_confident_hybrid_intent_does_not_reclassify_with_history(self):
        history = [
            {"role": "user", "content": "debug this Python function"},
            {"role": "assistant", "content": "The fix is ready."},
        ]
        with patch.dict(os.environ, {"ASRAR_ROUTING_MODE": "hybrid"}), \
                patch.object(classifier, "_model_classify", new_callable=AsyncMock) as model:
            result = await classifier.classify_task_async("please summarize this report", history=history)
        self.assertEqual(result.task_type, "summarization")
        model.assert_not_awaited()

    async def test_continuations_retain_task_locally_in_both_modes(self):
        history = [
            {"role": "user", "content": "debug this Python function"},
            {"role": "assistant", "content": "Should I add the fix?"},
            {"role": "user", "content": "yes"},
            {"role": "assistant", "content": "I will continue."},
        ]
        for mode in ("fast", "hybrid"):
            for prompt in ("ok", "ok, continue!", "go on", "yes please", "please proceed"):
                with self.subTest(mode=mode, prompt=prompt), \
                        patch.dict(os.environ, {"ASRAR_ROUTING_MODE": mode}), \
                        patch.object(classifier, "_model_classify", new_callable=AsyncMock) as model:
                    result = await classifier.classify_task_async(prompt, history=history)
                    self.assertEqual(result.task_type, "coding")
                    self.assertEqual(result.raw_input, prompt)
                    model.assert_not_awaited()

    async def test_new_explicit_request_overrides_previous_task(self):
        history = [{"role": "user", "content": "debug this Python function"}]
        result = await classifier.classify_task_async("search online for latest news", history=history)
        self.assertEqual(result.task_type, "web_research")

    async def test_uncertain_hybrid_request_still_uses_model(self):
        model_result = classifier.ClassifiedTask("ambiguous", "analysis", 0.8, source="model")
        with patch.dict(os.environ, {"ASRAR_ROUTING_MODE": "hybrid"}), \
                patch.object(classifier, "_model_classify", new=AsyncMock(return_value=model_result)) as model:
            result = await classifier.classify_task_async("something ambiguous")
        self.assertEqual(result.task_type, "analysis")
        model.assert_awaited_once()


class SpeculativeRaceEfficiencyTest(unittest.IsolatedAsyncioTestCase):
    async def test_tool_response_wins_without_waiting_for_text(self):
        async def blocked():
            await asyncio.Event().wait()

        tool_response = ("", [{"id": "read", "name": "read_file", "args": {"path": "app.py"}}])
        result = await orchestrator.race_models([
            blocked, AsyncMock(return_value=tool_response),
        ], timeout=0.1)
        self.assertEqual(result, (1, tool_response))

    async def test_fast_failure_does_not_cancel_healthy_candidate(self):
        async def healthy():
            await asyncio.sleep(0.01)
            return "useful answer", []

        failed = AsyncMock(return_value=("Provider error: ConnectError", []))
        success = AsyncMock(side_effect=healthy)
        index, result = await orchestrator.race_models([failed, success], timeout=0.5)
        self.assertEqual((index, result), (1, ("useful answer", [])))
        failed.assert_awaited_once()
        success.assert_awaited_once()

    async def test_all_failures_never_issue_an_extra_request(self):
        first = AsyncMock(side_effect=RuntimeError("first failure"))
        second = AsyncMock(side_effect=RuntimeError("second failure"))
        with self.assertRaisesRegex(RuntimeError, "first failure"):
            await orchestrator.race_models([first, second], timeout=0.5)
        first.assert_awaited_once()
        second.assert_awaited_once()

    async def test_timeout_cancels_and_drains_every_candidate_without_retry(self):
        for count in (1, 2):
            finished = []

            async def blocked():
                try:
                    await asyncio.Event().wait()
                finally:
                    finished.append(True)

            calls = [AsyncMock(side_effect=blocked) for _ in range(count)]
            with self.subTest(count=count), self.assertRaises(TimeoutError):
                await orchestrator.race_models(calls, timeout=0.02)
            self.assertEqual(len(finished), count)
            for call in calls:
                call.assert_awaited_once()

    async def test_winner_waits_for_loser_cleanup(self):
        finished = []

        async def loser():
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                finished.append(True)

        result = await orchestrator.race_models([
            AsyncMock(return_value=("ready", [])), loser,
        ])
        self.assertEqual(result, (0, ("ready", [])))
        self.assertEqual(finished, [True])


class StreamDeadlineTest(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_and_empty_tokens_do_not_restart_first_output_deadline(self):
        for event in ({"type": "status", "text": "waiting"}, {"type": "token", "text": ""}):
            closed = []

            async def stream():
                try:
                    for _ in range(20):
                        await asyncio.sleep(0.005)
                        yield event
                    yield {"type": "token", "text": "too late"}
                finally:
                    closed.append(True)

            with self.subTest(event=event), self.assertRaises(TimeoutError):
                async for _ in orchestrator.stream_with_retry(stream, first_event_timeout=0.02):
                    pass
            self.assertEqual(closed, [True])


class AgentSpeculativeReuseTest(unittest.IsolatedAsyncioTestCase):
    def _patch_agent(self, complete):
        from core import agent
        models = {key: {"id": key, "provider": key, "display_name": key}
                  for key in ("first", "second", "third")}
        decision = SimpleNamespace(selected_model_key="first", selected_model=models["first"],
                                   fallback_chain=["second", "third"], task_type="fast_chat", reason="test")
        stack = ExitStack()
        stack.enter_context(patch.dict(os.environ, {"ASRAR_SPECULATIVE_RACE": "1"}))
        stack.enter_context(patch.object(agent, "route_async", AsyncMock(return_value=decision)))
        stack.enter_context(patch.object(agent, "load_registry", return_value={"models": models}))
        stack.enter_context(patch.object(agent, "get_provider", return_value=SimpleNamespace(is_available=lambda: True)))
        stack.enter_context(patch.object(agent, "_llm_call_with_tools", new=complete))
        stack.enter_context(patch.object(agent, "_log"))
        stack.enter_context(patch.object(orchestrator, "is_available", return_value=True))
        stack.enter_context(patch.object(orchestrator, "record_success"))
        stack.enter_context(patch.object(orchestrator, "record_failure"))
        return agent, stack

    async def test_failed_raced_candidates_are_not_called_again_for_fallback(self):
        calls = []

        async def complete(provider=None, model_id=None, *args, **kwargs):
            calls.append(model_id)
            return ("Provider HTTP 503", []) if model_id != "third" else ("fallback answer", [])

        agent, patches = self._patch_agent(complete)
        with patches:
            result = await agent.run_task("hello")
        self.assertEqual(sorted(calls), ["first", "second", "third"])
        self.assertEqual(result["response"], "fallback answer")

    async def test_raced_tool_response_is_executed_without_repeating_initial_request(self):
        calls = []

        async def complete(provider=None, model_id=None, *args, **kwargs):
            messages = args[0] if args else kwargs["messages"]
            calls.append((model_id, len(messages)))
            if model_id == "second":
                await asyncio.Event().wait()
            if len(messages) == 1:
                return "", [{"id": "read", "name": "read_file", "args": {"path": "app.py"}}]
            return "finished after tool", []

        agent, patches = self._patch_agent(complete)
        with patches, patch.object(agent, "_call_tool", new=AsyncMock(return_value="contents")) as tool:
            result = await agent.run_task("hello")
        self.assertEqual(calls.count(("first", 1)), 1)
        self.assertEqual(len([call for call in calls if call[0] == "first"]), 2)
        self.assertEqual(result["response"], "finished after tool")
        tool.assert_awaited_once()

    async def test_timed_out_raced_candidates_are_not_replayed(self):
        calls = []

        async def complete(provider=None, model_id=None, *args, **kwargs):
            calls.append(model_id)
            if model_id != "third":
                await asyncio.Event().wait()
            return "fallback answer", []

        race = orchestrator.race_models

        async def short_race(callables, **kwargs):
            return await race(callables, timeout=0.02)

        agent, patches = self._patch_agent(complete)
        with patches, patch.object(orchestrator, "race_models", new=short_race):
            result = await agent.run_task("hello")
        self.assertEqual(sorted(calls), ["first", "second", "third"])
        self.assertEqual(result["response"], "fallback answer")


if __name__ == "__main__":
    unittest.main()
