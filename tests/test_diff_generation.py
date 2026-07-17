import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'backend'))

from tools import files


class WriteFileDiffTest(unittest.TestCase):
    def test_new_file_has_no_diff(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "new.py")
            r = files.write_file(path, "print('hi')")
        self.assertTrue(r.success)
        self.assertIsNone(r.diff)

    def test_overwriting_existing_file_produces_diff(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "existing.py")
            files.write_file(path, "print('old')")
            r = files.write_file(path, "print('new')", overwrite=True)
        self.assertTrue(r.success)
        self.assertIsNotNone(r.diff)
        self.assertIn("-print('old')", r.diff)
        self.assertIn("+print('new')", r.diff)

    def test_diff_uses_ab_prefixed_path_labels(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "a.py")
            files.write_file(path, "1")
            r = files.write_file(path, "2", overwrite=True)
        self.assertIn(f"a/{path}", r.diff)
        self.assertIn(f"b/{path}", r.diff)


class EditFileDiffTest(unittest.TestCase):
    def test_edit_produces_diff(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "app.py")
            files.write_file(path, "def foo():\n    return 1\n")
            r = files.edit_file(path, "return 1", "return 2")
        self.assertTrue(r.success)
        self.assertIsNotNone(r.diff)
        self.assertIn("-    return 1", r.diff)
        self.assertIn("+    return 2", r.diff)

    def test_failed_edit_has_no_diff(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "app.py")
            files.write_file(path, "def foo(): pass")
            r = files.edit_file(path, "not present anywhere", "x")
        self.assertFalse(r.success)
        self.assertIsNone(r.diff)

    def test_large_diff_is_truncated(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "big.py")
            before = "\n".join(f"line_{i} = {i}" for i in range(500))
            after = "\n".join(f"line_{i} = {i * 2}" for i in range(500))
            files.write_file(path, before)
            r = files.edit_file(path, before, after)
        self.assertTrue(r.success)
        self.assertIn("truncated", r.diff)


if __name__ == "__main__":
    unittest.main()
