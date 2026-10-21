"""Navigation exemptions must never approve commands with side effects."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from tools.shell import is_navigation_only
from core.hooks import _gate_destructive_commands


class NavigationPermissionsTest(unittest.TestCase):
    def test_literal_navigation_is_automatic(self):
        for command in ("cd", "cd ..", "cd /tmp", 'cd "folder with spaces"',
                        "cd -- /tmp", "cd -P /tmp", "cd /tmp && pwd", "cd .. && cd /tmp",
                        'cd "rm folder"'):
            with self.subTest(command=command):
                self.assertTrue(is_navigation_only(command))
                self.assertFalse(_gate_destructive_commands("run_command", {"command": command}, None).blocked)

    def test_side_effects_and_shell_expansion_are_not_exempt(self):
        for command in ("", "cd /tmp && rm file", "cd /tmp; chmod 777 file",
                        "cd $(touch file)", "cd `touch file`", "pwd > file", "cd /tmp > file",
                        "cd /tmp | tee file", "cd /tmp & touch file", "cd /tmp\ntouch file",
                        "cd /tmp &&", 'cd "unterminated', "cd /tmp || touch file"):
            with self.subTest(command=command):
                self.assertFalse(is_navigation_only(command))
