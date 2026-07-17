import asyncio
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'backend'))

from core import orchestrator


class CircuitBreakerTest(unittest.TestCase):
    def setUp(self):
        orchestrator.reset_circuit("test-provider")

    def test_starts_closed_and_available(self):
        self.assertTrue(orchestrator.is_available("test-provider"))
        self.assertEqual(orchestrator.circuit_status("test-provider")["state"], "closed")

    def test_stays_closed_below_failure_threshold(self):
        orchestrator.record_failure("test-provider")
        orchestrator.record_failure("test-provider")
        self.assertTrue(orchestrator.is_available("test-provider"))
        self.assertEqual(orchestrator.circuit_status("test-provider")["state"], "closed")

    def test_opens_at_failure_threshold(self):
        for _ in range(orchestrator.FAILURE_THRESHOLD):
            orchestrator.record_failure("test-provider")
        self.assertFalse(orchestrator.is_available("test-provider"))
        self.assertEqual(orchestrator.circuit_status("test-provider")["state"], "open")

    def test_success_resets_failure_count_and_closes(self):
        orchestrator.record_failure("test-provider")
        orchestrator.record_failure("test-provider")
        orchestrator.record_success("test-provider")
        status = orchestrator.circuit_status("test-provider")
        self.assertEqual(status["state"], "closed")
        self.assertEqual(status["consecutive_failures"], 0)

    def test_half_opens_after_cooldown_elapses(self):
        for _ in range(orchestrator.FAILURE_THRESHOLD):
            orchestrator.record_failure("test-provider")
        self.assertFalse(orchestrator.is_available("test-provider"))

        with patch("time.monotonic", return_value=time.monotonic() + 999):
            self.assertTrue(orchestrator.is_available("test-provider"))
            self.assertEqual(orchestrator.circuit_status("test-provider")["state"], "half_open")

    def test_failed_half_open_trial_backs_off_further(self):
        for _ in range(orchestrator.FAILURE_THRESHOLD):
            orchestrator.record_failure("test-provider")
        first_cooldown = orchestrator.circuit_status("test-provider")["cooldown_seconds"]

        with patch("time.monotonic", return_value=time.monotonic() + 999):
            orchestrator.is_available("test-provider")  # transitions to half_open
            orchestrator.record_failure("test-provider")  # trial fails

        status = orchestrator.circuit_status("test-provider")
        self.assertEqual(status["state"], "open")
        self.assertGreater(status["cooldown_seconds"], first_cooldown)

    def test_successful_half_open_trial_closes_circuit(self):
        for _ in range(orchestrator.FAILURE_THRESHOLD):
            orchestrator.record_failure("test-provider")

        with patch("time.monotonic", return_value=time.monotonic() + 999):
            orchestrator.is_available("test-provider")
            orchestrator.record_success("test-provider")

        status = orchestrator.circuit_status("test-provider")
        self.assertEqual(status["state"], "closed")
        self.assertEqual(status["cooldown_seconds"], orchestrator.BASE_COOLDOWN_SECONDS)

    def test_cooldown_is_capped(self):
        # Repeatedly fail half-open trials — cooldown should never exceed the cap.
        for _ in range(orchestrator.FAILURE_THRESHOLD):
            orchestrator.record_failure("test-provider")
        for _ in range(10):
            with patch("time.monotonic", return_value=time.monotonic() + 99999):
                orchestrator.is_available("test-provider")
                orchestrator.record_failure("test-provider")
        status = orchestrator.circuit_status("test-provider")
        self.assertLessEqual(status["cooldown_seconds"], orchestrator.MAX_COOLDOWN_SECONDS)

    def test_different_providers_have_independent_circuits(self):
        orchestrator.reset_circuit("provider-a")
        orchestrator.reset_circuit("provider-b")
        for _ in range(orchestrator.FAILURE_THRESHOLD):
            orchestrator.record_failure("provider-a")
        self.assertFalse(orchestrator.is_available("provider-a"))
        self.assertTrue(orchestrator.is_available("provider-b"))


class RetryLogicTest(unittest.TestCase):
    def test_retryable_status_codes(self):
        for code in ("429", "500", "502", "503", "504"):
            self.assertTrue(orchestrator.is_retryable_error(f"Provider HTTP {code}: oops"), code)

    def test_non_retryable_status_codes(self):
        for code in ("400", "401", "403", "404"):
            self.assertFalse(orchestrator.is_retryable_error(f"Provider HTTP {code}: oops"), code)

    def test_non_provider_error_is_not_retryable(self):
        self.assertFalse(orchestrator.is_retryable_error("some other error"))
        self.assertFalse(orchestrator.is_retryable_error(""))
        self.assertFalse(orchestrator.is_retryable_error(None))

    def test_retry_delay_honors_retry_after_header(self):
        delay = orchestrator.retry_delay_seconds(0, "Provider HTTP 429: rate limited (retry-after=5)")
        self.assertEqual(delay, 5.0)

    def test_retry_delay_caps_retry_after(self):
        delay = orchestrator.retry_delay_seconds(0, "Provider HTTP 429: rate limited (retry-after=999)")
        self.assertEqual(delay, 15.0)

    def test_retry_delay_exponential_backoff_without_header(self):
        self.assertEqual(orchestrator.retry_delay_seconds(0), 1)
        self.assertEqual(orchestrator.retry_delay_seconds(1), 2)
        self.assertEqual(orchestrator.retry_delay_seconds(2), 4)
        self.assertEqual(orchestrator.retry_delay_seconds(10), 8)  # capped

    def test_call_with_retry_succeeds_first_try_no_delay(self):
        calls = {"n": 0}

        async def fn():
            calls["n"] += 1
            return "ok", []

        text, tool_calls = asyncio.run(orchestrator.call_with_retry(fn))
        self.assertEqual(text, "ok")
        self.assertEqual(calls["n"], 1)

    def test_call_with_retry_retries_retryable_error_then_succeeds(self):
        calls = {"n": 0}

        async def fn():
            calls["n"] += 1
            if calls["n"] < 3:
                return "Provider HTTP 429: rate limited", []
            return "recovered", []

        with patch("core.orchestrator.retry_delay_seconds", return_value=0):
            text, tool_calls = asyncio.run(orchestrator.call_with_retry(fn, max_retries=3))
        self.assertEqual(text, "recovered")
        self.assertEqual(calls["n"], 3)

    def test_call_with_retry_gives_up_after_max_retries(self):
        calls = {"n": 0}

        async def fn():
            calls["n"] += 1
            return "Provider HTTP 500: server error", []

        with patch("core.orchestrator.retry_delay_seconds", return_value=0):
            text, tool_calls = asyncio.run(orchestrator.call_with_retry(fn, max_retries=2))
        self.assertEqual(text, "Provider HTTP 500: server error")
        self.assertEqual(calls["n"], 3)  # initial attempt + 2 retries

    def test_call_with_retry_does_not_retry_non_retryable_error(self):
        calls = {"n": 0}

        async def fn():
            calls["n"] += 1
            return "Provider HTTP 401: unauthorized", []

        with patch("core.orchestrator.retry_delay_seconds", return_value=0):
            text, tool_calls = asyncio.run(orchestrator.call_with_retry(fn, max_retries=3))
        self.assertEqual(calls["n"], 1)  # no retries wasted on a bad-request-style error

    def test_call_with_retry_does_not_retry_when_tool_calls_present(self):
        calls = {"n": 0}

        async def fn():
            calls["n"] += 1
            return "Provider HTTP 429: rate limited", [{"id": "1", "name": "read_file", "args": {}}]

        with patch("core.orchestrator.retry_delay_seconds", return_value=0):
            text, tool_calls = asyncio.run(orchestrator.call_with_retry(fn, max_retries=3))
        self.assertEqual(calls["n"], 1)
        self.assertEqual(len(tool_calls), 1)

    def test_on_retry_callback_invoked(self):
        calls = {"n": 0}
        retries_seen = []

        async def fn():
            calls["n"] += 1
            if calls["n"] < 2:
                return "Provider HTTP 503: unavailable", []
            return "ok", []

        with patch("core.orchestrator.retry_delay_seconds", return_value=0):
            asyncio.run(orchestrator.call_with_retry(
                fn, max_retries=2, on_retry=lambda attempt, delay, err: retries_seen.append((attempt, err))
            ))
        self.assertEqual(len(retries_seen), 1)
        self.assertEqual(retries_seen[0][0], 0)


class ConcurrentToolExecutionTest(unittest.TestCase):
    def test_read_only_calls_run_concurrently(self):
        """Two 100ms 'reads' should take ~100ms total if concurrent, not ~200ms."""
        async def slow_read(name, args, idx):
            await asyncio.sleep(0.1)
            return f"{name}-result"

        tool_calls = [
            {"name": "read_file", "args": {"path": "a.py"}},
            {"name": "search_code", "args": {"pattern": "x"}},
        ]

        start = time.monotonic()
        results = asyncio.run(orchestrator.run_tool_calls(tool_calls, slow_read))
        elapsed = time.monotonic() - start

        self.assertEqual(results, ["read_file-result", "search_code-result"])
        self.assertLess(elapsed, 0.18)  # well under 0.2s (sequential would be ~0.2s+)

    def test_mutating_calls_run_sequentially(self):
        order = []

        async def track(name, args, idx):
            order.append(f"start-{name}")
            await asyncio.sleep(0.01)
            order.append(f"end-{name}")
            return name

        tool_calls = [
            {"name": "write_file", "args": {}},
            {"name": "edit_file", "args": {}},
        ]
        asyncio.run(orchestrator.run_tool_calls(tool_calls, track))

        # Sequential means each call fully completes before the next starts.
        self.assertEqual(order, ["start-write_file", "end-write_file", "start-edit_file", "end-edit_file"])

    def test_mixed_batch_preserves_result_order(self):
        async def echo(name, args, idx):
            return f"{name}:{idx}"

        tool_calls = [
            {"name": "search_code", "args": {}},
            {"name": "find_files", "args": {}},
            {"name": "write_file", "args": {}},
            {"name": "read_file", "args": {}},
        ]
        results = asyncio.run(orchestrator.run_tool_calls(tool_calls, echo))
        self.assertEqual(results, ["search_code:0", "find_files:1", "write_file:2", "read_file:3"])

    def test_index_passed_matches_position_in_original_list(self):
        seen_indices = []

        async def capture(name, args, idx):
            seen_indices.append(idx)
            return "ok"

        tool_calls = [
            {"name": "read_file", "args": {}},
            {"name": "list_dir", "args": {}},
            {"name": "run_command", "args": {}},
            {"name": "search_code", "args": {}},
        ]
        asyncio.run(orchestrator.run_tool_calls(tool_calls, capture))
        self.assertEqual(sorted(seen_indices), [0, 1, 2, 3])

    def test_empty_tool_calls_returns_empty_results(self):
        async def unused(name, args, idx):
            raise AssertionError("should never be called")

        results = asyncio.run(orchestrator.run_tool_calls([], unused))
        self.assertEqual(results, [])

    def test_single_read_only_call_still_works(self):
        async def echo(name, args, idx):
            return "solo-result"

        results = asyncio.run(orchestrator.run_tool_calls([{"name": "read_file", "args": {}}], echo))
        self.assertEqual(results, ["solo-result"])


if __name__ == "__main__":
    unittest.main()
