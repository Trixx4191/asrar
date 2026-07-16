import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'backend' / 'core'))


class MemoryVerificationStateTest(unittest.TestCase):
    """Point memory at a throwaway DB per test so these don't collide with
    real conversation data or with each other."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        import core.memory as memory
        self.memory = memory
        self._patcher = patch.object(memory, "DB_PATH", Path(self._tmp.name))
        self._patcher.start()
        memory.init_db()
        self.conv_id = memory.create_conversation("test")

    def tearDown(self):
        self._patcher.stop()
        os.unlink(self._tmp.name)

    def test_fresh_conversation_is_not_dirty(self):
        state = self.memory.get_verification_state(self.conv_id)
        self.assertFalse(state["dirty"])
        self.assertEqual(state["dirty_files"], [])

    def test_mark_dirty_sets_state_and_accumulates_files(self):
        self.memory.mark_dirty(self.conv_id, "main.py")
        self.memory.mark_dirty(self.conv_id, "utils.py")
        state = self.memory.get_verification_state(self.conv_id)
        self.assertTrue(state["dirty"])
        self.assertEqual(set(state["dirty_files"]), {"main.py", "utils.py"})

    def test_mark_dirty_does_not_duplicate_same_file(self):
        self.memory.mark_dirty(self.conv_id, "main.py")
        self.memory.mark_dirty(self.conv_id, "main.py")
        state = self.memory.get_verification_state(self.conv_id)
        self.assertEqual(state["dirty_files"], ["main.py"])

    def test_mark_verified_clears_dirty_and_nudged(self):
        self.memory.mark_dirty(self.conv_id, "main.py")
        self.memory.set_nudged(self.conv_id)
        self.memory.mark_verified(self.conv_id, {"tool": "run_tests", "success": True})
        state = self.memory.get_verification_state(self.conv_id)
        self.assertFalse(state["dirty"])
        self.assertFalse(state["nudged"])
        self.assertEqual(state["dirty_files"], [])
        self.assertEqual(state["last_result"], {"tool": "run_tests", "success": True})

    def test_set_nudged_prevents_repeat_without_new_change(self):
        self.memory.mark_dirty(self.conv_id, "main.py")
        self.assertFalse(self.memory.get_verification_state(self.conv_id)["nudged"])
        self.memory.set_nudged(self.conv_id)
        self.assertTrue(self.memory.get_verification_state(self.conv_id)["nudged"])

    def test_new_dirty_file_resets_nudged(self):
        self.memory.mark_dirty(self.conv_id, "main.py")
        self.memory.set_nudged(self.conv_id)
        self.memory.mark_dirty(self.conv_id, "other.py")  # a fresh change earns a fresh nudge
        self.assertFalse(self.memory.get_verification_state(self.conv_id)["nudged"])


class TrackVerificationStateTest(unittest.TestCase):
    """_track_verification_state is the glue between a tool call's result
    string and memory's verification table — check it reacts to the right
    tools and ignores failed calls."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        import core.memory as memory
        import core.agent as agent
        self.memory = memory
        self.agent = agent
        self._patcher = patch.object(memory, "DB_PATH", Path(self._tmp.name))
        self._patcher.start()
        memory.init_db()
        self.conv_id = memory.create_conversation("test")

    def tearDown(self):
        self._patcher.stop()
        os.unlink(self._tmp.name)

    def test_successful_edit_of_code_file_marks_dirty(self):
        self.agent._track_verification_state(
            "edit_file", {"path": "app.py"}, "Edited app.py successfully", self.conv_id
        )
        self.assertTrue(self.memory.get_verification_state(self.conv_id)["dirty"])

    def test_successful_edit_of_non_code_file_does_not_mark_dirty(self):
        self.agent._track_verification_state(
            "edit_file", {"path": "README.md"}, "Edited README.md successfully", self.conv_id
        )
        self.assertFalse(self.memory.get_verification_state(self.conv_id)["dirty"])

    def test_failed_edit_does_not_mark_dirty(self):
        self.agent._track_verification_state(
            "edit_file", {"path": "app.py"}, "Error: file not found", self.conv_id
        )
        self.assertFalse(self.memory.get_verification_state(self.conv_id)["dirty"])

    def test_run_tests_clears_dirty_state(self):
        self.memory.mark_dirty(self.conv_id, "app.py")
        self.agent._track_verification_state(
            "run_tests", {}, "✅ pytest (python -m pytest -q): 4 passed, 0 failed", self.conv_id
        )
        self.assertFalse(self.memory.get_verification_state(self.conv_id)["dirty"])

    def test_create_project_with_code_file_marks_dirty(self):
        self.agent._track_verification_state(
            "create_project",
            {"name": "demo", "files_dict": {"main.py": "print(1)", "README.md": "hi"}},
            "Project created at ~/Downloads/demo",
            self.conv_id,
        )
        state = self.memory.get_verification_state(self.conv_id)
        self.assertTrue(state["dirty"])
        self.assertIn("demo/main.py", state["dirty_files"])


class RunTaskVerificationNudgeTest(unittest.TestCase):
    """End-to-end: a coding task that edits a file but never verifies it
    should get exactly one nudge, then a final answer — not an infinite
    loop and not a silent pass."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        import core.memory as memory
        self.memory = memory
        self._patcher = patch.object(memory, "DB_PATH", Path(self._tmp.name))
        self._patcher.start()
        memory.init_db()

    def tearDown(self):
        self._patcher.stop()
        os.unlink(self._tmp.name)

    def test_nudge_fires_once_then_accepts_final_answer(self):
        import core.agent as agent

        class DummyProvider:
            def is_available(self):
                return True

        call_count = {"n": 0}

        async def fake_llm_call_with_tools(provider, model_id, messages, system, provider_name):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First turn: "edit" a code file (tool call marks state dirty
                # via the real _call_tool -> _track_verification_state path).
                return "", [{"id": "tc1", "name": "edit_file",
                             "args": {"path": "app.py", "old_string": "a", "new_string": "b"}}]
            if call_count["n"] == 2:
                # Tries to stop without verifying — should get nudged instead
                # of being accepted immediately.
                return "Done, I edited app.py.", []
            # After the nudge, it verifies and gives a real final answer.
            return "Verified with tests, all passing. app.py is updated.", []

        with patch.dict(os.environ, {"GROQ_API_KEY": "test"}), \
             patch("core.agent.get_provider", return_value=DummyProvider()), \
             patch("core.agent._llm_call_with_tools", new=fake_llm_call_with_tools), \
             patch("core.agent.files.edit_file") as mock_edit:
            mock_edit.return_value = type("R", (), {"success": True, "content": "Edited app.py", "error": None})()

            conv_id = self.memory.create_conversation("test")
            response = asyncio.run(agent.run_task(
                "fix the bug in app.py", history=[], force_model=None, conversation_id=conv_id,
            ))

        # Nudge should have consumed one extra call (edit -> premature stop
        # -> nudge -> real final), and the accepted answer is the post-nudge one.
        self.assertEqual(call_count["n"], 3)
        self.assertIn("Verified", response["response"])


if __name__ == "__main__":
    unittest.main()
