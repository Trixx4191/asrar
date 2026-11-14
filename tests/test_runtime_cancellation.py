"""Stopping an API stream must immediately close providers and task context."""

import asyncio
import importlib
from contextlib import ExitStack
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from api.routes import chat
from core import agent, orchestrator, runtime


class RuntimeCancellationTest(unittest.IsolatedAsyncioTestCase):
    def patch_agent(self, provider):
        model = {"id": "test", "provider": "test", "display_name": "Test"}
        decision = SimpleNamespace(selected_model_key="test", selected_model=model,
                                   fallback_chain=[], reason="test", task_type="coding")
        stack = ExitStack()
        stack.enter_context(patch.object(agent, "route_async", AsyncMock(return_value=decision)))
        stack.enter_context(patch.object(agent, "load_registry", return_value={"models": {"test": model}}))
        stack.enter_context(patch.object(agent, "get_provider", return_value=provider))
        stack.enter_context(patch.object(agent, "_log"))
        stack.enter_context(patch.object(orchestrator, "is_available", return_value=True))
        stack.enter_context(patch.object(orchestrator, "record_success"))
        stack.enter_context(patch.object(orchestrator, "record_failure"))
        return stack

    async def test_api_body_close_after_meta_closes_inner_task_in_same_context(self):
        closed = []

        async def inner(*args, **kwargs):
            try:
                yield {"meta": True, "model": "Test"}
                yield {"token": "partial"}
                await asyncio.Event().wait()
            finally:
                closed.append(True)

        with patch.object(agent, "_run_task_stream", new=inner), \
             patch.object(chat.memory, "create_conversation", return_value="chat"), \
             patch.object(chat.memory, "get_messages", return_value=[]), \
             patch.object(chat.memory, "add_message") as add, \
             patch.object(chat.memory, "log_event"):
            response = await chat.chat_stream(chat.ChatRequest(message="hello"))
            stream = response.body_iterator
            try:
                self.assertIn('"conversation_id"', await anext(stream))
                self.assertIn('"meta"', await anext(stream))
                self.assertIsNotNone(runtime.current())
            finally:
                await stream.aclose()
            self.assertEqual(closed, [True])
            self.assertIsNone(runtime.current())
            # A cancelled partial response must not be saved as a final answer.
            add.assert_called_once_with("chat", "user", "hello")

    async def test_direct_root_stream_close_releases_provider_and_scope(self):
        closed = []

        async def stream_complete(**kwargs):
            try:
                yield {"type": "token", "text": "partial"}
                await asyncio.Event().wait()
            finally:
                closed.append(True)

        provider = SimpleNamespace(is_available=lambda: True, stream_complete=stream_complete)
        with self.patch_agent(provider):
            stream = agent.run_task_stream("inspect the code")
            try:
                while True:
                    event = await anext(stream)
                    if event.get("token"):
                        break
                self.assertIsNotNone(runtime.current())
            finally:
                await stream.aclose()
            self.assertEqual(closed, [True])
            self.assertIsNone(runtime.current())

    async def test_cancellation_while_waiting_for_provider_closes_every_layer(self):
        entered = asyncio.Event()
        closed = []
        cleaned = []

        async def stream_complete(**kwargs):
            try:
                entered.set()
                await asyncio.Event().wait()
                yield {"type": "token", "text": "unreachable"}
            finally:
                closed.append(True)

        async def consume():
            try:
                async for _ in agent.run_task_stream("inspect the code"):
                    pass
            finally:
                cleaned.append(runtime.current() is None)

        provider = SimpleNamespace(is_available=lambda: True, stream_complete=stream_complete)
        with self.patch_agent(provider):
            consumer = asyncio.create_task(consume())
            try:
                await asyncio.wait_for(entered.wait(), timeout=1)
                consumer.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await consumer
            finally:
                if not consumer.done():
                    consumer.cancel()
                    await asyncio.gather(consumer, return_exceptions=True)
            self.assertEqual(closed, [True])
            self.assertEqual(cleaned, [True])
            self.assertIsNone(runtime.current())

    async def test_subscription_http_close_runs_each_runtimes_interrupt_cleanup(self):
        for name in ("codex", "claude", "kimi", "muse"):
            closed = []

            async def subscription(*args, **kwargs):
                try:
                    yield {"meta": True, "model": name}
                    await asyncio.Event().wait()
                finally:
                    closed.append(True)

            module = importlib.import_module("core." + name + "_chat")
            with self.subTest(runtime=name), \
                 patch.object(module, "stream_chat", new=subscription), \
                 patch.object(chat.memory, "create_conversation", return_value="chat"):
                response = await chat.chat_stream(
                    chat.ChatRequest(message="hello", runtime=name), x_asrar_client="desktop-v1",
                )
                stream = response.body_iterator
                try:
                    self.assertIn('"conversation_id"', await anext(stream))
                    self.assertIn('"meta"', await anext(stream))
                finally:
                    await stream.aclose()
                self.assertEqual(closed, [True])

    async def test_api_error_return_closes_inner_generator(self):
        closed = []

        async def inner(*args, **kwargs):
            try:
                yield {"error": "offline"}
                await asyncio.Event().wait()
            finally:
                closed.append(True)

        with patch.object(agent, "_run_task_stream", new=inner), \
             patch.object(chat.memory, "create_conversation", return_value="chat"), \
             patch.object(chat.memory, "get_messages", return_value=[]), \
             patch.object(chat.memory, "add_message"), \
             patch.object(chat.memory, "log_event"):
            response = await chat.chat_stream(chat.ChatRequest(message="hello"))
            events = [event async for event in response.body_iterator]
            self.assertTrue(any('"error": "offline"' in event for event in events))
            self.assertEqual(closed, [True])
            self.assertIsNone(runtime.current())


if __name__ == "__main__":
    unittest.main()
