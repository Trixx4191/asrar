import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'backend'))

from tools import testing


class DetectTestCommandTest(unittest.TestCase):
    def test_detects_pytest_from_test_file(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "test_thing.py").write_text("def test_ok(): assert True")
            framework, command = testing.detect_test_command(d)
        self.assertEqual(framework, "pytest")
        self.assertIn("pytest", command)

    def test_detects_npm_test_script(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
            framework, command = testing.detect_test_command(d)
        self.assertEqual(framework, "npm")
        self.assertIn("npm test", command)

    def test_prefers_pnpm_when_lockfile_present(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
            (Path(d) / "pnpm-lock.yaml").write_text("")
            _, command = testing.detect_test_command(d)
        self.assertEqual(command, "pnpm test")

    def test_no_scripts_test_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "package.json").write_text(json.dumps({"scripts": {"build": "webpack"}}))
            framework, command = testing.detect_test_command(d)
        self.assertIsNone(framework)
        self.assertIsNone(command)

    def test_go_mod_detected(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "go.mod").write_text("module example.com/foo")
            framework, command = testing.detect_test_command(d)
        self.assertEqual(framework, "go")
        self.assertEqual(command, "go test ./...")

    def test_empty_dir_detects_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            framework, command = testing.detect_test_command(d)
        self.assertIsNone(framework)
        self.assertIsNone(command)


class ParseSummaryTest(unittest.TestCase):
    def test_pytest_all_passed(self):
        stats = testing._parse_summary("pytest", "===== 5 passed in 0.12s =====")
        self.assertEqual(stats, {"passed": 5, "failed": 0, "total": 5})

    def test_pytest_with_failures(self):
        stats = testing._parse_summary("pytest", "===== 3 passed, 2 failed in 0.30s =====")
        self.assertEqual(stats, {"passed": 3, "failed": 2, "total": 5})

    def test_jest_summary(self):
        stats = testing._parse_summary("npm", "Tests:       1 failed, 4 passed, 5 total")
        self.assertEqual(stats, {"passed": 4, "failed": 1, "total": 5})

    def test_unrecognized_output_returns_empty(self):
        stats = testing._parse_summary("pytest", "some unrelated log output")
        self.assertEqual(stats, {})


class RunTestsNoSuiteTest(unittest.TestCase):
    def test_run_tests_reports_missing_suite_instead_of_guessing(self):
        with tempfile.TemporaryDirectory() as d:
            result = asyncio.run(testing.run_tests(path=d))
        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)
        self.assertIn("No recognized test suite", result.error)


class ExecuteCodeTest(unittest.TestCase):
    def test_execute_python_success(self):
        result = asyncio.run(testing.execute_code("print('hello')", language="python"))
        self.assertTrue(result.success)
        self.assertIn("hello", result.stdout)

    def test_execute_python_failure_surfaces_stderr(self):
        result = asyncio.run(testing.execute_code("raise ValueError('boom')", language="python"))
        self.assertFalse(result.success)
        self.assertIn("boom", result.stderr)

    def test_unsupported_language_rejected(self):
        result = asyncio.run(testing.execute_code("echo hi", language="cobol"))
        self.assertFalse(result.success)
        self.assertIn("Unsupported language", result.error)


class IsCodeFileTest(unittest.TestCase):
    def test_recognizes_common_code_extensions(self):
        for path in ("main.py", "app.js", "index.tsx", "server.go", "lib.rs"):
            self.assertTrue(testing.is_code_file(path), path)

    def test_rejects_non_code_extensions(self):
        for path in ("notes.txt", "README.md", "data.csv", ""):
            self.assertFalse(testing.is_code_file(path), path)


if __name__ == "__main__":
    unittest.main()
