"""Subscription protocol, approvals, persistence, and local-origin boundaries."""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock

import httpx
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from core import codex_bridge, codex_chat, memory, preferences
from core.codex_bridge import CodexBridge, CodexError, login_url


class SubscriptionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bridge = CodexBridge()
        self.patches = [
            patch.object(codex_bridge, "executable", return_value=str(ROOT / "tests/fixtures/codex_app_server.py")),
            patch.object(codex_bridge, "AUTH_HOME", self.root / "auth"),
            patch.object(codex_chat, "AUTH_HOME", self.root / "auth"),
            patch.object(codex_chat, "bridge", self.bridge),
            patch.object(preferences, "PREFERENCES_PATH", self.root / "preferences.json"),
            patch.object(memory, "DB_PATH", self.root / "test.db"),
        ]
        for p in self.patches:
            p.start()
        memory.init_db()
        preferences.save_preferences({"workspace": str(self.root)})
        self.conv = memory.create_conversation("Protocol test")

    async def asyncTearDown(self):
        await self.bridge.close()
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    async def collect(self, text="hello", **kwargs):
        return [event async for event in codex_chat.stream_chat(text, self.conv, **kwargs)]

    async def test_handshake_and_account_allowlist(self):
        accounts = await asyncio.gather(self.bridge.account(), self.bridge.account())
        self.assertEqual(accounts[0], {"type": "chatgpt", "email": "test@example.invalid", "plan": "plus"})
        self.assertNotIn("NEVER_EXPOSE", str(accounts))
        if os.name != "nt":
            self.assertEqual((self.root / "auth").stat().st_mode & 0o777, 0o700)

    async def test_stream_persists_authoritative_reply_plan_and_session(self):
        events = await self.collect()
        self.assertEqual(events[-1]["response"], "Hello from Codex.")
        self.assertTrue(any("codex_diff" in e for e in events))
        self.assertEqual(memory.get_plan(self.conv)[0]["status"], "completed")
        messages = memory.get_messages_full(self.conv)
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertEqual(messages[-1]["content"], "Hello from Codex.")
        self.assertEqual(codex_chat.session_for(self.conv)["thread_id"], "thread-test")
        self.assertFalse(self.bridge.active)

    async def test_resume_after_bridge_restart(self):
        await self.collect()
        await self.bridge.close()
        events = await self.collect("second task")
        self.assertTrue(events[-1]["done"])
        self.assertEqual(len(memory.get_messages_full(self.conv)), 4)

    async def test_approval_is_scoped_and_single_use(self):
        stream = codex_chat.stream_chat("needs approval", self.conv)
        ticket = None
        async for event in stream:
            if event.get("approval_required"):
                ticket = event["action_id"]
                break
        with self.assertRaises(CodexError):
            await self.bridge.decide("wrong-conversation", ticket, True)
        await self.bridge.decide(self.conv, ticket, False)
        with self.assertRaises(CodexError):
            await self.bridge.decide(self.conv, ticket, True)
        remaining = [event async for event in stream]
        self.assertTrue(remaining[-1]["done"])

    async def test_stream_close_cancels_actual_turn(self):
        stream = codex_chat.stream_chat("wait", self.conv)
        await anext(stream)
        self.assertEqual(self.bridge.active[self.conv]["turn_id"], "turn-test")
        with patch.object(self.bridge, "schedule_interrupt", wraps=self.bridge.schedule_interrupt) as stop:
            await stream.aclose()
            stop.assert_called_once_with(self.conv)
        self.assertFalse(self.bridge.active)
        self.assertFalse(self.bridge.listeners)

    async def test_task_cancellation_still_sends_runtime_interrupt(self):
        stream = codex_chat.stream_chat("wait", self.conv)
        await anext(stream)
        with patch.object(self.bridge, "_send", wraps=self.bridge._send) as send:
            pending = asyncio.create_task(anext(stream))
            await asyncio.sleep(0)
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
            await asyncio.sleep(.03)
            self.assertTrue(any(call.args[0].get("method") == "turn/interrupt" for call in send.call_args_list))
        self.assertFalse(self.bridge.active)

    async def test_context_files_are_scoped_to_workspace(self):
        file = self.root / "example.py"
        file.write_text("print('hello')")
        events = await self.collect(context_files=["example.py"])
        self.assertTrue(events[-1]["done"])
        self.assertIn("example.py", memory.get_messages_full(self.conv)[0]["content"])
        new = memory.create_conversation("outside context")
        events = [event async for event in codex_chat.stream_chat("hello", new, context_files=[str(ROOT / "README.md")])]
        self.assertIn("inside the selected workspace", events[0]["error"])

    async def test_questions_require_complete_scoped_answers(self):
        stream = codex_chat.stream_chat("ask a question", self.conv)
        async for event in stream:
            if event.get("question_required"):
                ticket = event["action_id"]
                break
        with self.assertRaises(CodexError):
            await self.bridge.decide(self.conv, ticket, True)
        with self.assertRaises(CodexError):
            await self.bridge.answer(self.conv, ticket, {})
        with self.assertRaises(CodexError):
            await self.bridge.answer("wrong", ticket, {"preference": "Simple"})
        await self.bridge.answer(self.conv, ticket, {"preference": "Simple"})
        remaining = [event async for event in stream]
        self.assertTrue(remaining[-1]["done"])
        self.assertFalse(self.bridge.approvals)

    async def test_unsupported_server_request_fails_closed(self):
        with patch.object(self.bridge, "_send", new_callable=AsyncMock) as send:
            await self.bridge._dispatch({"id": "unknown", "method": "unexpected/grantAccess", "params": {}})
            response = send.call_args.args[0]
            self.assertIn("error", response)
            self.assertNotIn("result", response)

    async def test_subprocess_does_not_inherit_api_credentials(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "must-not-inherit", "CODEX_API_KEY": "must-not-inherit"}):
            import asyncio as aio
            real_spawn = aio.create_subprocess_exec
            with patch.object(codex_bridge.asyncio, "create_subprocess_exec", wraps=real_spawn) as spawn:
                await self.bridge.account()
                environment = spawn.call_args.kwargs["env"]
                self.assertNotIn("OPENAI_API_KEY", environment)
                self.assertNotIn("CODEX_API_KEY", environment)
                self.assertEqual(environment["CODEX_HOME"], str(self.root / "auth"))

    async def test_second_turn_in_same_conversation_is_rejected(self):
        stream = codex_chat.stream_chat("wait", self.conv)
        await anext(stream)
        events = await self.collect()
        self.assertIn("already has", events[0]["error"])
        self.assertIn(self.conv, self.bridge.active)
        await stream.aclose()

    async def test_switch_workspace_requires_new_chat(self):
        await self.collect()
        directory = self.root / "other"
        directory.mkdir()
        preferences.save_preferences({"workspace": str(directory)})
        events = await self.collect()
        self.assertIn("another workspace", events[0]["error"])
        self.assertEqual(len(memory.get_messages_full(self.conv)), 2)

    async def test_account_session_change_requires_new_chat(self):
        await self.collect()
        (self.root / "auth/asrar-session-id").write_text("new-account-session")
        events = await self.collect()
        self.assertIn("another workspace or account", events[0]["error"])

    async def test_api_conversation_not_silently_migrated(self):
        memory.add_message(self.conv, "user", "API history")
        events = await self.collect()
        self.assertIn("API connection", events[0]["error"])

    async def test_signed_out_has_no_api_fallback(self):
        await self.bridge.logout()
        events = await self.collect()
        self.assertIn("Connect your ChatGPT", events[0]["error"])
        self.assertEqual(memory.get_messages_full(self.conv), [])

    async def test_failed_turn_persisted_with_honest_error(self):
        events = await self.collect("fail")
        self.assertTrue(any(e.get("error") == "Usage limit reached" for e in events))
        self.assertIn("Task failed", memory.get_messages_full(self.conv)[-1]["content"])

    async def test_unknown_model_and_effort_rejected_before_turn(self):
        events = await self.collect(model="made-up")
        self.assertIn("no longer available", events[0]["error"])
        events = await self.collect(effort="imaginary")
        self.assertIn("does not support", events[0]["error"])
        self.assertEqual(memory.get_messages_full(self.conv), [])

    async def test_logout_blocked_during_turn(self):
        stream = codex_chat.stream_chat("wait", self.conv)
        await anext(stream)
        with self.assertRaises(CodexError):
            await self.bridge.logout()
        await stream.aclose()

    async def test_login_can_be_cancelled_and_blocks_new_tasks(self):
        login = await self.bridge.begin_login()
        self.assertEqual(login["loginId"], "login-test")
        events = await self.collect()
        self.assertIn("pending sign-in", events[0]["error"])
        await self.bridge.cancel_login()
        self.assertIsNone(self.bridge.login)
        self.assertTrue((await self.collect())[-1]["done"])

    async def test_local_api_origin_guards_and_preferences(self):
        from api.server import app
        from api.routes import connections
        with patch.object(connections, "bridge", self.bridge):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
                response = await client.post("/connections/codex/login", json={})
                self.assertEqual(response.status_code, 403)
                headers = {"X-Asrar-Client": "desktop-v1"}
                response = await client.get("/connections/codex", headers=headers)
                self.assertTrue(response.json()["connected"])
                self.assertNotIn("NEVER_EXPOSE", response.text)
                response = await client.post("/connections/codex/login", json={}, headers={**headers, "Origin": "https://evil.invalid"})
                self.assertEqual(response.status_code, 403)
                response = await client.get("/connections/codex", headers={**headers, "Host": "evil.invalid:8000"})
                self.assertEqual(response.status_code, 403)
                response = await client.patch("/connections/preferences", json={"access_mode": "danger-full-access"}, headers=headers)
                self.assertEqual(response.status_code, 422)
                response = await client.patch("/connections/preferences", json={"agent_name": "My Asrar"}, headers=headers)
                self.assertEqual(response.json()["agent_name"], "My Asrar")
                response = await client.post("/chat/stream", json={"runtime": "codex", "message": "hi"})
                self.assertEqual(response.status_code, 403)

    async def test_subscription_chat_cannot_use_api_endpoint(self):
        await self.collect()
        from api.routes.chat import ChatRequest, chat_stream
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as caught:
            await chat_stream(ChatRequest(message="next", runtime="api", conversation_id=self.conv))
        self.assertEqual(caught.exception.status_code, 409)


class PreferenceValidationTest(unittest.TestCase):
    def test_nonexistent_workspace_and_unknown_settings_rejected(self):
        with self.assertRaises(ValidationError):
            preferences.Preferences(workspace="/no-such-asrar-workspace-1234")
        with self.assertRaises(ValidationError):
            preferences.Preferences(api_key="secret")

    def test_login_urls_reject_credentials_and_untrusted_hosts(self):
        for url in ["http://auth.openai.com/a", "https://auth.openai.com.evil.invalid/a", "https://user:pass@auth.openai.com/a", "file:///tmp/token"]:
            with self.assertRaises(CodexError):
                login_url(url)
        self.assertEqual(login_url("https://auth.openai.com/a"), "https://auth.openai.com/a")


if __name__ == "__main__":
    unittest.main()
