import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from core import skills


class SkillsTest(unittest.TestCase):
    def test_lists_valid_repository_skills(self):
        available = skills.list_skills()
        names = {skill["name"] for skill in available}
        self.assertIn("code-review", names)
        self.assertIn("commit", names)
        self.assertEqual(len(names), len(available))

    def test_loads_skill_content(self):
        skill = skills.load_skill("code-review")
        self.assertIsNotNone(skill)
        self.assertIn("# Code Review", skill["content"])
        self.assertEqual(skill["name"], "code-review")

    def test_rejects_path_traversal_and_unknown_skills(self):
        self.assertIsNone(skills.load_skill("../README"))
        self.assertIsNone(skills.load_skill("missing-skill"))


if __name__ == "__main__":
    unittest.main()