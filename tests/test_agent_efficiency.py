"""Regression coverage for repeated work and real read-only concurrency."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core import agent, orchestrator
from core.progress import ToolProgressGuard
from providers.base import ProviderResponse


class ProgressGuardTest(unittest.TestCase):
    def test_new_ids_and_argument_order_do_not_hide_repeated_work(self):
        guard = ToolProgressGuard()
        for index in range(3):
            args = {"path": ".", "pattern": "name"} if index % 2 else {"pattern": "name", "path": "."}
            self.assertEqual(guard.stalled([
                {"id": f"call-{index}", "name": "search_code", "args": args},
            ], ["No matches"]), index == 2)

    def test_changing_observations_and_intervening_edits_reset_the_count(self):
        guard = ToolProgressGuard()
        read = [{"name": "read_file", "args": {"path": "app.py"}}]
        self.assertFalse(guard.stalled(read, ["old contents"]))
        self.assertFalse(guard.stalled(read, ["old contents"]))
        self.assertFalse(guard.stalled(read, ["new contents"]))
        self.assertFalse(guard.stalled(read, ["new contents"]))
        self.assertFalse(guard.observe("edit_file", {"path": "app.py"}, "edited"))
        self.assertFalse(guard.stalled(read, ["new contents"]))
        self.assertFalse(guard.stalled(read, ["new contents"]))
        guard.reset()
        self.assertFalse(guard.stalled(read, ["new contents"]))

    def test_results_beyond_ui_preview_are_compared(self):
        guard = ToolProgressGuard()
        calls = [{"name": "read_file", "args": {"path": "app.py"}}]
        for suffix in ["a", "a", "b", "b", "c"]:
            self.assertFalse(guard.stalled(calls, ["prefix" * 1000 + suffix]))

    def test_successful_commands_and_live_process_polls_are_exempt(self):
        for name in ["run_command", "inspect_process", "list_processes", "fetch_page", "run_tests"]:
            with self.subTest(tool=name):
                guard = ToolProgressGuard()
                for _ in range(5):
                    self.assertFalse(agent._stalled_tool_round(guard, [
                        {"name": name, "args": {}},
                    ], ["still running"]))

    def test_repeated_failures_of_mutating_tools_are_bounded(self):
        guard = ToolProgressGuard()
        for index in range(3):
            self.assertEqual(agent._stalled_tool_round(guard, [
                {"name": "edit_file", "args": {"path": "missing.py"}},
            ], ["Error: file not found"]), index == 2)


class AgentEfficiencyTest(unittest.IsolatedAsyncioTestCase):
    def _patch_agent(self, provider):
        model = {"id": "test", "provider": "test", "display_name": "Test model"}
        decision = SimpleNamespace(
            selected_model_key="test", selected_model=model, fallback_chain=[],
            reason="test", task_type="coding",
        )
        stack = ExitStack()
        stack.enter_context(patch.object(agent, "route_async", AsyncMock(return_value=decision)))
        stack.enter_context(patch.object(agent, "load_registry", return_value={"models": {"test": model}}))
        stack.enter_context(patch.object(agent, "get_provider", return_value=provider))
        stack.enter_context(patch.object(agent, "_log"))
        stack.enter_context(patch.object(orchestrator, "is_available", return_value=True))
        stack.enter_context(patch.object(orchestrator, "record_success"))
        return stack

    async def test_buffered_task_stops_repeated_rounds_before_ten_model_calls(self):
        count = 0

        async def repeat(**kwargs):
            nonlocal count
            count += 1
            return "Looking again", [{"id": f"call-{count}", "name": "read_file", "args": {"path": "app.py"}}]

        with self._patch_agent(SimpleNamespace(is_available=lambda: True)), \
             patch.object(agent, "_llm_call_with_tools", new=repeat), \
             patch.object(agent, "_call_tool", AsyncMock(return_value="same contents")) as execute:
            result = await agent.run_task("read the file")
        self.assertEqual(count, 3)
        self.assertEqual(execute.await_count, 3)
        self.assertTrue(result["incomplete"])
        self.assertIn("identical results", result["response"])

    async def test_streamed_task_stops_repeated_rounds_and_surfaces_incomplete(self):
        calls = []

        async def stream(**kwargs):
            calls.append(kwargs)
            response = ProviderResponse("", "test", "test", tool_calls=[{
                "id": f"call-{len(calls)}", "name": "search_code", "args": {"pattern": "missing"},
            }])
            yield {"type": "done", "response": response}

        provider = SimpleNamespace(is_available=lambda: True, stream_complete=stream)
        with self._patch_agent(provider), \
             patch.object(agent, "_call_tool", AsyncMock(return_value="No matches")):
            events = [event async for event in agent.run_task_stream("find the function")]
        self.assertEqual(len(calls), 3)
        self.assertEqual(sum("tool_result" in event for event in events), 3)
        self.assertTrue(events[-1]["done"])
        self.assertTrue(events[-1]["incomplete"])

    async def test_identical_queries_with_changing_results_can_finish_normally(self):
        repeated = ("", [{"id": "call", "name": "read_file", "args": {"path": "app.py"}}])
        completion = AsyncMock(side_effect=[repeated, repeated, repeated, ("Completed", [])])
        with self._patch_agent(SimpleNamespace(is_available=lambda: True)), \
             patch.object(agent, "_llm_call_with_tools", new=completion), \
             patch.object(agent, "_call_tool", AsyncMock(side_effect=["one", "two", "three"])):
            result = await agent.run_task("inspect the results")
        self.assertEqual(completion.await_count, 4)
        self.assertEqual(result["response"], "Completed")
        self.assertFalse(result["incomplete"])

    async def test_round_limit_marks_both_paths_incomplete_despite_intermediate_narration(self):
        call = {"id": "call", "name": "run_command", "args": {"command": "echo running"}}
        completion = AsyncMock(return_value=("Finishing up", [call]))

        async def stream(**kwargs):
            yield {"type": "token", "text": "Finishing up"}
            yield {"type": "done", "response": ProviderResponse("Finishing up", "test", "test", tool_calls=[call])}

        provider = SimpleNamespace(is_available=lambda: True, stream_complete=stream)
        for streamed in [False, True]:
            with self.subTest(streamed=streamed), self._patch_agent(provider), \
                 patch.object(agent, "_llm_call_with_tools", new=completion), \
                 patch.object(agent, "_call_tool", AsyncMock(return_value="running")) as execute:
                if streamed:
                    result = [event async for event in agent.run_task_stream("check jobs")][-1]
                else:
                    result = await agent.run_task("check jobs")
            self.assertEqual(execute.await_count, 10)
            self.assertTrue(result["incomplete"])
            self.assertIn("tool-round limit", result["response"])

    async def test_synchronous_file_reads_really_overlap_and_release_the_event_loop(self):
        barrier = threading.Barrier(2)
        worker_ids = []
        loop_thread = threading.get_ident()

        def read(path, max_chars):
            worker_ids.append(threading.get_ident())
            barrier.wait(timeout=2)
            return SimpleNamespace(success=True, content=path)

        calls = [{"name": "read_file", "args": {"path": path}} for path in ["first", "second"]]
        # Own the test's workers explicitly, so runner teardown does not need
        # to create another executor-shutdown thread in restricted sandboxes.
        with ThreadPoolExecutor(max_workers=2) as executor, \
             patch.object(asyncio.get_running_loop(), "_default_executor", executor), \
             patch.object(agent.files, "read_file", side_effect=read):
            results = await asyncio.wait_for(orchestrator.run_tool_calls(
                calls, lambda name, args, index: agent._call_tool(name, args),
            ), timeout=3)
        self.assertEqual(results, ["first", "second"])
        self.assertTrue(all(worker != loop_thread for worker in worker_ids))


if __name__ == "__main__":
    unittest.main()
