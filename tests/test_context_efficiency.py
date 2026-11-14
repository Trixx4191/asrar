"""Context must save replayed tokens without changing the task or tool protocol."""

import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from core import context
from providers.base import Message


def openai_round(index, result):
    identifier = f"read-{index}"
    return [{"role": "assistant", "content": "", "tool_calls": [{
        "id": identifier, "type": "function", "function": {"name": "read_file", "arguments": '{"path":"app.py"}'},
    }]}, {"role": "tool", "name": "read_file", "tool_call_id": identifier, "content": result}]


def assert_paired(test, messages):
    seen = set()
    for message in messages:
        for call in message.get("tool_calls") or []:
            seen.add(call["id"])
        if message.get("role") == "tool" and message.get("tool_call_id"):
            test.assertIn(message["tool_call_id"], seen)
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_use":
                    seen.add(block["id"])
                if block.get("type") == "tool_result":
                    test.assertIn(block["tool_use_id"], seen)


class PreparedContextTest(unittest.TestCase):
    def test_large_tool_transcript_is_bounded_without_changing_call_ids(self):
        messages = [{"role": "user", "content": "Fix the parser and preserve comments."}]
        for index in range(8):
            messages += openai_round(index, "HEAD\n" + "source code\n" * 2000 + "\nTAIL")
        before = copy.deepcopy(messages)
        prepared = context.prepare_messages(messages, max_chars=22000)
        self.assertLessEqual(context.context_chars(prepared), 22000)
        self.assertLess(context.context_chars(prepared), context.context_chars(messages) / 4)
        self.assertEqual(messages, before)
        self.assertEqual(prepared[0], messages[0])
        self.assertEqual([m.get("tool_call_id") for m in prepared], [m.get("tool_call_id") for m in messages])
        assert_paired(self, prepared)

    def test_recent_log_preserves_failure_at_end(self):
        messages = [{"role": "user", "content": "run the failing test"}] + openai_round(0, "START\n" + "output\n" * 10000 + "FAILED test_parser: expected 2, got 3")
        prepared = context.prepare_messages(messages, max_chars=8000)
        result = prepared[-1]["content"]
        self.assertTrue(result.startswith("START"))
        self.assertTrue(result.endswith("FAILED test_parser: expected 2, got 3"))
        self.assertIn("omitted from model context", result)

    def test_full_user_instructions_and_pending_code_arguments_are_never_cut(self):
        request = "User requirements.\n" * 3000 + "FINAL REQUIREMENT: preserve my license."
        arguments = '{"path":"app.py","content":"' + "x" * 15000 + '"}'
        pending = {"role": "assistant", "content": None, "tool_calls": [{
            "id": "write", "type": "function", "function": {"name": "write_file", "arguments": arguments},
        }]}
        messages = [{"role": "user", "content": request}] + openai_round(0, "log" * 5000) + [pending]
        prepared = context.prepare_messages(messages, max_chars=4000)
        self.assertEqual(prepared[0]["content"], request)
        self.assertEqual(prepared[-1], pending)
        # The budget is intentionally soft if mandatory user/call text alone exceeds it.
        self.assertGreater(context.context_chars(prepared), 4000)

    def test_anthropic_results_are_compressed_without_mutating_native_blocks(self):
        native = [{"role": "user", "content": "inspect"}, {
            "role": "assistant", "content": [{"type": "tool_use", "id": "read", "name": "read_file", "input": {"path": "app.py"}}],
        }, {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "read", "content": [
            {"type": "text", "text": "begin" + "x" * 50000 + "failure at end"},
        ], "is_error": True}]}]
        before = copy.deepcopy(native)
        prepared = context.prepare_messages(native, max_chars=6000)
        self.assertLessEqual(context.context_chars(prepared), 6000)
        self.assertEqual(native, before)
        self.assertEqual(prepared[-1]["content"][0]["tool_use_id"], "read")
        self.assertTrue(prepared[-1]["content"][0]["is_error"])
        self.assertTrue(prepared[-1]["content"][0]["content"][0]["text"].endswith("failure at end"))
        assert_paired(self, prepared)

    def test_dataclass_messages_retain_tool_metadata(self):
        messages = [Message(role="assistant", content="", tool_calls=[{"id": "read", "function": {"name": "read_file"}}]),
                    Message(role="tool", content="contents", tool_call_id="read", name="read_file")]
        prepared = context.prepare_messages(messages)
        self.assertEqual(prepared[-1]["tool_call_id"], "read")
        self.assertEqual(prepared[-1]["name"], "read_file")
        assert_paired(self, prepared)

    def test_short_transcript_keeps_result_verbatim(self):
        messages = [{"role": "user", "content": "inspect"}] + openai_round(0, "def f():\n    return 42\n")
        self.assertEqual(context.prepare_messages(messages), messages)

    def test_older_source_remains_complete_when_it_still_fits_the_budget(self):
        source = "def preserved_source():\n" + "    important_line = True\n" * 400
        messages = [{"role": "user", "content": "inspect both"}] + openai_round(0, source) + openai_round(1, "latest result")
        prepared = context.prepare_messages(messages)
        self.assertEqual(prepared[2]["content"], source)




class ConversationCompactionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        context._summary_cache.clear()

    async def test_compaction_does_not_split_openai_tool_rounds(self):
        messages = [{"role": "user", "content": "Original goal: preserve the API."}, {"role": "assistant", "content": "Ready."},
                    {"role": "user", "content": "Implement the fix now."}]
        for index in range(7):
            messages += openai_round(index, "file text" * 500)
        messages.append({"role": "assistant", "content": "Finished."})
        compacted = await context.compact_history(messages)
        self.assertTrue(compacted.truncated)
        self.assertIn({"role": "user", "content": "Original goal: preserve the API."}, compacted.messages)
        self.assertIn({"role": "user", "content": "Implement the fix now."}, compacted.messages)
        assert_paired(self, compacted.messages)

    async def test_compaction_does_not_treat_anthropic_tool_result_as_user_request(self):
        messages = [{"role": "user", "content": "Older goal."}, {"role": "assistant", "content": "Ready."}, {"role": "user", "content": "Continue."}]
        for index in range(7):
            messages += [{"role": "assistant", "content": [{"type": "tool_use", "id": f"read-{index}", "name": "read_file", "input": {"path": "app.py"}}]},
                         {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"read-{index}", "content": "source" * 500}]}]
        compacted = await context.compact_history(messages)
        self.assertTrue(compacted.truncated)
        assert_paired(self, compacted.messages)

    async def test_user_interruption_cannot_orphan_pending_tool_calls(self):
        messages = [{"role": "user", "content": "Start."}] + openai_round(0, "done")[:1]
        messages += [{"role": "user", "content": "Also preserve tests."}]
        messages += openai_round(0, "done")[1:]
        for index in range(1, 8):
            messages += openai_round(index, "done")
        compacted = await context.compact_history(messages)
        assert_paired(self, compacted.messages)
        self.assertIn({"role": "user", "content": "Also preserve tests."}, compacted.messages)

    async def test_few_large_turns_still_compact_and_keep_latest_request_in_full(self):
        latest = "Latest full requirements\n" + "exact acceptance criterion\n" * 1000
        messages = [{"role": "user", "content": "Older task\n" * 5000}, {"role": "assistant", "content": "Older answer\n" * 3000},
                    {"role": "user", "content": latest}, {"role": "assistant", "content": "Will do."}]
        compacted = await context.compact_history(messages)
        self.assertTrue(compacted.truncated)
        self.assertIn({"role": "user", "content": latest}, compacted.messages)
        self.assertIn(messages[0], compacted.messages)
        self.assertLess(context.context_chars(compacted.messages), context.context_chars(messages))

    async def test_summary_keeps_newer_user_updates_and_never_needs_a_model_by_default(self):
        history = []
        for index in range(20):
            history += [{"role": "user", "content": f"Requirement {index}: {index * 7}"}, {"role": "assistant", "content": f"Outcome {index}"}]
        with patch.object(context, "_llm_summarize", new=AsyncMock()) as llm:
            compacted = await context.compact_history(history)
        llm.assert_not_awaited()
        self.assertIn(history[0], compacted.messages)
        self.assertIn(history[30], compacted.messages)
        self.assertIn("Outcome 15", compacted.summary)

    async def test_same_length_history_edits_invalidate_summary_cache(self):
        history = []
        for index in range(10):
            history += [{"role": "user", "content": f"Goal {index}"}, {"role": "assistant", "content": "Done."}]
        with patch.object(context, "set_cached_summary") as cache:
            first = await context.compact_history(history, conversation_id="test")
            unchanged = await context.compact_history(history, conversation_id="test")
            history[0]["content"] = "Changed original goal"
            changed = await context.compact_history(history, conversation_id="test")
        self.assertEqual(first.summary, unchanged.summary)
        self.assertIn({"role": "user", "content": "Changed original goal"}, changed.messages)
        self.assertEqual(cache.call_count, 2)

    async def test_long_original_constraints_and_intermediate_overrides_survive_verbatim(self):
        original = "Opening requirement.\n" + "Detailed requirement.\n" * 150 + "MIDDLE CONSTRAINT: preserve the license text exactly.\n" + "More constraints.\n" * 180
        override = "Override the earlier deployment target: use staging, never production."
        history = [{"role": "user", "content": original}, {"role": "assistant", "content": "Planning."}]
        for index in range(14):
            request = override if index == 3 else f"Continue phase {index}."
            history += [{"role": "user", "content": request}, {"role": "assistant", "content": f"Phase {index} complete."}]
        compacted = await context.compact_history(history)
        self.assertTrue(compacted.truncated)
        self.assertIn({"role": "user", "content": original}, compacted.messages)
        self.assertIn({"role": "user", "content": override}, compacted.messages)
        self.assertGreater(len(original), 5000)
        assert_paired(self, compacted.messages)

    async def test_pinning_mixed_anthropic_request_does_not_orphan_its_result(self):
        history = [{"role": "user", "content": "Initial task."}, {
            "role": "assistant", "content": [{"type": "tool_use", "id": "read", "name": "read_file", "input": {}}],
        }, {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "read", "content": "file contents"},
                                             {"type": "text", "text": "USER CORRECTION: preserve tests."}]}]
        for index in range(10):
            history += [{"role": "user", "content": f"Phase {index}."}, {"role": "assistant", "content": "Done."}]
        compacted = await context.compact_history(history)
        self.assertIn({"role": "user", "content": [{"type": "text", "text": "USER CORRECTION: preserve tests."}]}, compacted.messages)
        assert_paired(self, compacted.messages)

    async def test_system_instructions_survive_older_turn_compaction(self):
        system = {"role": "system", "content": "Never expose credentials."}
        history = [system]
        for index in range(10):
            history += [{"role": "user", "content": f"Goal {index}"}, {"role": "assistant", "content": "Done."}]
        compacted = await context.compact_history(history)
        self.assertEqual(compacted.messages[0], system)


if __name__ == "__main__":
    unittest.main()
