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


class MemoryPendingActionTest(unittest.TestCase):
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

    def test_create_and_get_pending_action(self):
        action_id = self.memory.create_pending_action(
            self.conv_id, "run_command", {"command": "rm old.log"}, reason="needs approval"
        )
        action = self.memory.get_pending_action(action_id)
        self.assertIsNotNone(action)
        self.assertEqual(action["status"], "pending")
        self.assertEqual(action["args"]["command"], "rm old.log")
        self.assertEqual(action["conversation_id"], self.conv_id)

    def test_get_latest_pending_action_filters_by_tool(self):
        self.memory.create_pending_action(self.conv_id, "run_command", {"command": "a"})
        newest = self.memory.create_pending_action(self.conv_id, "run_command", {"command": "b"})
        latest = self.memory.get_latest_pending_action(self.conv_id, tool="run_command")
        self.assertEqual(latest["id"], newest)

    def test_get_latest_pending_action_ignores_resolved(self):
        first = self.memory.create_pending_action(self.conv_id, "run_command", {"command": "a"})
        self.memory.resolve_pending_action(first, "approved")
        latest = self.memory.get_latest_pending_action(self.conv_id, tool="run_command")
        self.assertIsNone(latest)

    def test_resolve_pending_action_succeeds_once(self):
        action_id = self.memory.create_pending_action(self.conv_id, "run_command", {"command": "a"})
        first = self.memory.resolve_pending_action(action_id, "approved")
        second = self.memory.resolve_pending_action(action_id, "approved")
        self.assertTrue(first)
        self.assertFalse(second)  # can't double-resolve — protects against a double-click race

    def test_resolve_unknown_action_returns_false(self):
        self.assertFalse(self.memory.resolve_pending_action("nonexistent", "approved"))


class HookCreatesPendingActionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        import core.memory as memory
        import core.hooks as hooks
        self.memory = memory
        self.hooks = hooks
        self._patcher = patch.object(memory, "DB_PATH", Path(self._tmp.name))
        self._patcher.start()
        memory.init_db()
        self.conv_id = memory.create_conversation("test")

    def tearDown(self):
        self._patcher.stop()
        os.unlink(self._tmp.name)

    def test_destructive_command_blocked_and_pending_action_created(self):
        result = self.hooks.run_pre_tool_hooks(
            "run_command", {"command": "sudo rm -rf /some/dir"}, self.conv_id
        )
        self.assertTrue(result.blocked)
        self.assertTrue(result.action_id)

        pending = self.memory.get_pending_action(result.action_id)
        self.assertIsNotNone(pending)
        self.assertEqual(pending["args"]["command"], "sudo rm -rf /some/dir")
        self.assertEqual(pending["status"], "pending")

    def test_confirmed_true_bypasses_hook_without_pending_action(self):
        result = self.hooks.run_pre_tool_hooks(
            "run_command", {"command": "sudo apt install foo", "confirmed": True}, self.conv_id
        )
        self.assertFalse(result.blocked)

    def test_safe_command_not_blocked(self):
        result = self.hooks.run_pre_tool_hooks("run_command", {"command": "ls -la"}, self.conv_id)
        self.assertFalse(result.blocked)
        self.assertEqual(result.action_id, "")


class ApproveEndpointLogicTest(unittest.TestCase):
    """Exercises the same logic the /chat/approve route uses, without
    spinning up FastAPI — the important behaviors are: approval runs the
    EXACT stored command, denial never runs anything, and an action can't
    be resolved twice."""

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

    def test_approved_action_runs_exact_stored_command(self):
        from tools import shell

        action_id = self.memory.create_pending_action(
            self.conv_id, "run_command", {"command": "echo approved-output", "timeout": 10}
        )
        pending = self.memory.get_pending_action(action_id)
        self.assertEqual(pending["status"], "pending")

        resolved = self.memory.resolve_pending_action(action_id, "approved")
        self.assertTrue(resolved)

        result = asyncio.run(shell.run(pending["args"]["command"], timeout=10, override=True))
        self.assertTrue(result.success)
        self.assertIn("approved-output", result.stdout)

    def test_denied_action_is_resolved_without_running_anything(self):
        action_id = self.memory.create_pending_action(
            self.conv_id, "run_command", {"command": "echo should-not-run"}
        )
        resolved = self.memory.resolve_pending_action(action_id, "denied")
        self.assertTrue(resolved)
        pending = self.memory.get_pending_action(action_id)
        self.assertEqual(pending["status"], "denied")

    def test_double_approval_only_resolves_once(self):
        action_id = self.memory.create_pending_action(self.conv_id, "run_command", {"command": "echo x"})
        first = self.memory.resolve_pending_action(action_id, "approved")
        second = self.memory.resolve_pending_action(action_id, "approved")
        self.assertTrue(first)
        self.assertFalse(second)


if __name__ == "__main__":
    unittest.main()
