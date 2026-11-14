"""Regression cases for intent routing without extra classification requests."""

import os
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core import classifier, supervisor
from core.router import RoutingDecision


class OperationRoutingTest(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_operations_win_over_incidental_subject_words_without_model_calls(self):
        examples = {
            "build a website": "coding",
            "fix the UI": "coding",
            "why is this code crashing": "coding",
            "write a report about Python code": "document_work",
            "search the codebase for this function": "coding",
            "search online for a Python function": "web_research",
            "edit report.py to print the summary": "coding",
            "make the app faster": "coding",
            "please review the architecture": "coding",
            "now summarize this report": "summarization",
            "analyze the csv file": "analysis",
            "describe this screenshot": "multimodal",
            "create a GitHub pull request": "document_work",
            "hello": "fast_chat",
            "thanks!": "fast_chat",
        }
        for mode in ("fast", "hybrid"):
            with patch.dict(os.environ, {"ASRAR_ROUTING_MODE": mode}), \
                    patch.object(classifier, "_model_classify", new_callable=AsyncMock) as model:
                for prompt, expected in examples.items():
                    with self.subTest(mode=mode, prompt=prompt):
                        result = await classifier.classify_task_async(prompt)
                        self.assertEqual(result.task_type, expected)
                        self.assertTrue(result.explicit_intent)
                model.assert_not_awaited()

    async def test_only_referential_followups_inherit_the_previous_task(self):
        history = [{"role": "user", "content": "build a website"}]
        self.assertEqual((await classifier.classify_task_async("make it faster", history=history)).task_type, "coding")
        self.assertEqual((await classifier.classify_task_async("bananas are yellow", history=history)).task_type, "general")

    async def test_continuation_does_not_resurrect_a_task_before_a_new_general_topic(self):
        history = [
            {"role": "user", "content": "build a website"},
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "bananas are yellow"},
            {"role": "assistant", "content": "continue?"},
        ]
        self.assertEqual((await classifier.classify_task_async("yes", history=history)).task_type, "general")

    async def test_block_content_history_uses_text_without_attachment_metadata(self):
        history = [{"role": "user", "content": [
            {"type": "image", "source": {"url": "https://irrelevant/research.jpg"}},
            {"type": "text", "text": "build a website"},
        ]}]
        self.assertEqual((await classifier.classify_task_async("continue", history=history)).task_type, "coding")

    def test_pasted_source_is_bounded_and_preserves_both_ends(self):
        payload = "build a website\n" + "x" * 200000 + "\nkeep this final instruction"
        snippet = classifier._classification_text(payload)
        self.assertEqual(len(snippet), classifier.MAX_CLASSIFICATION_CHARS)
        self.assertTrue(snippet.startswith("build a website"))
        self.assertTrue(snippet.endswith("keep this final instruction"))
        self.assertEqual(classifier.classify_task(payload).task_type, "coding")

    async def test_hybrid_skips_known_failed_classifier_connections(self):
        models = {
            "groq-fast": {"provider": "groq", "id": "bad", "validation": {"status": "failed"}},
            "mistral-small": {"provider": "mistral", "id": "healthy"},
        }
        provider = SimpleNamespace(is_available=lambda: True, complete=AsyncMock(return_value=SimpleNamespace(
            success=True, content='{"task_type":"analysis","confidence":0.8}',
        )))
        with patch("core.router.load_registry", return_value={"models": models}), \
                patch("core.router.get_available_models", return_value=set(models)), \
                patch("core.orchestrator.is_available", return_value=True), \
                patch("providers.get_provider", return_value=provider) as get:
            result = await classifier._model_classify("ambiguous")
        self.assertEqual(result.task_type, "analysis")
        self.assertEqual(get.call_args.args, ("mistral",))
        self.assertEqual(provider.complete.call_args.kwargs["model_id"], "healthy")


class ClarificationRoutingTest(unittest.IsolatedAsyncioTestCase):
    def setup_route(self, *, circuit_available=True, pending_status="passed"):
        state = {"awaiting_clarification": True, "pending_model_key": "pending", "task_type": "coding"}
        models = {
            "pending": {"provider": "old", "id": "pending", "display_name": "Pending", "enabled": True,
                        "validation": {"status": pending_status}},
            "backup": {"provider": "new", "id": "backup", "display_name": "Backup", "enabled": True},
        }
        fresh = RoutingDecision("web_research", "backup", models["backup"], [], "fresh")
        stack = ExitStack()
        stack.enter_context(patch.object(supervisor.memory, "get_state", return_value=state))
        stack.enter_context(patch.object(supervisor.memory, "log_event"))
        clear = stack.enter_context(patch.object(supervisor.memory, "clear_awaiting"))
        stack.enter_context(patch.object(supervisor, "load_registry", return_value={
            "models": models, "task_model_map": {"coding": ["pending", "backup"]},
        }))
        stack.enter_context(patch.object(supervisor, "get_available_models", return_value=set(models)))
        stack.enter_context(patch.object(supervisor.orchestrator, "is_available", return_value=circuit_available))
        stack.enter_context(patch.object(supervisor, "get_provider", return_value=SimpleNamespace(is_available=lambda: True)))
        sync = stack.enter_context(patch.object(supervisor, "route", return_value=fresh))
        async_route = stack.enter_context(patch.object(supervisor, "route_async", AsyncMock(return_value=fresh)))
        return stack, clear, sync, async_route

    async def test_explicit_new_task_escapes_pending_question_in_both_paths(self):
        for streamed in (False, True):
            stack, clear, sync, async_route = self.setup_route()
            with self.subTest(streamed=streamed), stack:
                if streamed:
                    result = await supervisor.decide_route_async("chat", "search online for recent news")
                    async_route.assert_awaited_once()
                else:
                    result = supervisor.decide_route("chat", "search online for recent news")
                    sync.assert_called_once()
                self.assertEqual(result.selected_model_key, "backup")
                self.assertFalse(result.sticky)
                clear.assert_called_once_with("chat")

    async def test_clarifying_answer_keeps_model_and_local_fallbacks(self):
        for streamed in (False, True):
            stack, clear, sync, async_route = self.setup_route()
            with self.subTest(streamed=streamed), stack:
                result = (await supervisor.decide_route_async("chat", "Python") if streamed
                          else supervisor.decide_route("chat", "Python"))
                self.assertEqual(result.selected_model_key, "pending")
                self.assertTrue(result.sticky)
                self.assertEqual(result.fallback_chain, ["backup"])
                self.assertEqual(result.task_type, "coding")
                sync.assert_not_called()
                async_route.assert_not_awaited()
                clear.assert_not_called()

    async def test_referential_clarification_keeps_model_even_with_an_operation_verb(self):
        for prompt in ("write it in Python", "please make it a Python app", "use this function"):
            stack, clear, sync, async_route = self.setup_route()
            with self.subTest(prompt=prompt), stack:
                result = await supervisor.decide_route_async("chat", prompt)
                self.assertTrue(result.sticky)
                self.assertEqual(result.selected_model_key, "pending")
                async_route.assert_not_awaited()
                clear.assert_not_called()

    async def test_unhealthy_pending_model_does_not_hold_the_conversation(self):
        for kwargs in ({"circuit_available": False}, {"pending_status": "failed"}):
            stack, clear, sync, async_route = self.setup_route(**kwargs)
            with self.subTest(**kwargs), stack:
                result = await supervisor.decide_route_async("chat", "yes")
                self.assertEqual(result.selected_model_key, "backup")
                self.assertFalse(result.sticky)
                async_route.assert_awaited_once()
                clear.assert_called_once_with("chat")

    async def test_manual_override_keeps_its_no_fallback_contract(self):
        stack, clear, sync, async_route = self.setup_route()
        with stack:
            await supervisor.decide_route_async("chat", "Python", force_model="backup")
            async_route.assert_awaited_once_with("Python", force_model="backup", history=None)
            clear.assert_not_called()


if __name__ == "__main__":
    unittest.main()
