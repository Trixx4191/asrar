import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'backend'))

from tools import codesearch


def _write(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


class SearchCodeTest(unittest.TestCase):
    def test_finds_matches_across_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "app.py", "def handler():\n    return route_request(x)\n")
            _write(root, "utils.py", "def route_request(x):\n    return x\n")
            r = codesearch.search_code("route_request", path=str(root))
        self.assertTrue(r.success)
        self.assertEqual(len(r.matches), 2)
        paths = {m.path for m in r.matches}
        self.assertEqual(paths, {"app.py", "utils.py"})

    def test_case_insensitive_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "a.py", "class UserAuth: pass")
            r = codesearch.search_code("userauth", path=str(root))
        self.assertEqual(len(r.matches), 1)

    def test_case_sensitive_when_requested(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "a.py", "class UserAuth: pass")
            r = codesearch.search_code("userauth", path=str(root), case_sensitive=True)
        self.assertEqual(len(r.matches), 0)

    def test_glob_filters_by_filename(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "a.py", "TODO: fix this")
            _write(root, "b.js", "// TODO: fix this too")
            r = codesearch.search_code("TODO", path=str(root), glob="*.py")
        self.assertEqual(len(r.matches), 1)
        self.assertEqual(r.matches[0].path, "a.py")

    def test_skips_noise_directories(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "node_modules/pkg/index.js", "needle")
            _write(root, "src/index.js", "needle")
            r = codesearch.search_code("needle", path=str(root))
        self.assertEqual(len(r.matches), 1)
        self.assertEqual(r.matches[0].path, "src/index.js")

    def test_invalid_regex_reports_error(self):
        with tempfile.TemporaryDirectory() as d:
            r = codesearch.search_code("(unclosed", path=d)
        self.assertFalse(r.success)
        self.assertIn("Invalid regex", r.error)

    def test_missing_path_reports_error(self):
        r = codesearch.search_code("x", path="/definitely/not/a/real/path/xyz")
        self.assertFalse(r.success)
        self.assertIn("does not exist", r.error)

    def test_truncates_at_max_matches(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "big.py", "\n".join(f"needle {i}" for i in range(50)))
            r = codesearch.search_code("needle", path=str(root), max_matches=10)
        self.assertEqual(len(r.matches), 10)
        self.assertTrue(r.truncated)


class FindFilesTest(unittest.TestCase):
    def test_finds_files_by_glob(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "test_a.py", "")
            _write(root, "test_b.py", "")
            _write(root, "main.py", "")
            r = codesearch.find_files("test_*.py", path=str(root))
        self.assertTrue(r.success)
        self.assertEqual(set(r.paths), {"test_a.py", "test_b.py"})

    def test_matches_nested_paths(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "backend/core/agent.py", "")
            _write(root, "frontend/src/app.jsx", "")
            r = codesearch.find_files("*.py", path=str(root))
        self.assertEqual(r.paths, ["backend/core/agent.py"])

    def test_not_a_directory_reports_error(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "file.txt"
            f.write_text("hi")
            r = codesearch.find_files("*.txt", path=str(f))
        self.assertFalse(r.success)
        self.assertIn("Not a directory", r.error)


class FormatOutputTest(unittest.TestCase):
    def test_format_search_result_groups_by_file(self):
        r = codesearch.SearchResult(
            success=True,
            matches=[
                codesearch.Match(path="a.py", line=1, text="foo"),
                codesearch.Match(path="a.py", line=5, text="foo again"),
            ],
            files_searched=3,
        )
        out = codesearch.format_search_result(r)
        self.assertIn("a.py:", out)
        self.assertIn("1: foo", out)
        self.assertIn("5: foo again", out)

    def test_format_search_result_no_matches(self):
        r = codesearch.SearchResult(success=True, matches=[], files_searched=5)
        out = codesearch.format_search_result(r)
        self.assertIn("No matches", out)

    def test_format_find_files_result_empty(self):
        r = codesearch.FindFilesResult(success=True, paths=[])
        self.assertIn("No matching files", codesearch.format_find_files_result(r))


if __name__ == "__main__":
    unittest.main()
