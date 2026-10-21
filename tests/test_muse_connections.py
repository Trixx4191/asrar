"""Meta account isolation and real subprocess MSP lifecycle, with no model billing."""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from core import muse_bridge, muse_chat, memory, preferences
from core.muse_bridge import MuseBridge, MuseError, login_url
from core.codex_chat import session_for


class MuseConnectionsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bridge = MuseBridge()
        self.patches = [
            patch.object(muse_bridge, 'AUTH_HOME', self.root / 'auth'),
            patch.object(muse_bridge, 'executable', return_value=str(ROOT / 'tests/fixtures/muse_server.py')),
            patch.object(muse_chat, 'bridge', self.bridge),
            patch.object(memory, 'DB_PATH', self.root / 'test.db'),
            patch.object(preferences, 'PREFERENCES_PATH', self.root / 'preferences.json'),
        ]
        for p in self.patches: p.start()
        memory.init_db()
        preferences.save_preferences({'runtime': 'muse', 'workspace': str(self.root)})
        await self.login()
        self.conv = memory.create_conversation('Muse test')

    async def login(self):
        flow = await self.bridge.begin_login()
        self.assertEqual(flow['userCode'], 'TEST-CODE')
        await self.bridge.request('test/finishLogin')

    async def asyncTearDown(self):
        await self.bridge.close()
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    async def collect(self, message='hello', **kwargs):
        return [e async for e in muse_chat.stream_chat(message, self.conv, **kwargs)]

    async def test_authoritative_reply_and_resume_without_duplicate_history(self):
        events = await self.collect(effort='max')
        self.assertEqual(events[-1]['response'], 'Hello from Muse.')
        self.assertEqual(session_for(self.conv)['runtime'], 'muse')
        await self.bridge.close()
        events = await self.collect()
        self.assertEqual(events[-1]['response'], 'Hello from Muse.')
        self.assertNotIn('OLD REPLAY', str(events))
        self.assertEqual(len(memory.get_messages_full(self.conv)), 4)
        self.assertFalse(self.bridge.active)

    async def test_catalog_exposes_only_meta_models_and_supported_efforts(self):
        models = await self.bridge.models()
        self.assertEqual(len(models), 2)
        self.assertIn({'reasoningEffort': 'max'}, models[0]['supportedReasoningEfforts'])
        self.assertEqual(models[1]['displayName'], 'Muse Wonder Full')
        self.assertIn('not available', (await self.collect(model='made-up'))[0]['error'])
        self.assertIn('does not support', (await self.collect(effort='imaginary'))[0]['error'])
        self.assertEqual(memory.get_messages_full(self.conv), [])

    async def test_login_cancel_logout_and_account_scoping(self):
        status = await self.bridge.status()
        self.assertTrue(status['connected'])
        self.assertNotIn('NEVER_EXPOSE', str(status))
        await self.collect()
        await self.bridge.logout()
        self.assertFalse((await self.bridge.status())['connected'])
        self.assertIn('Sign in', (await self.collect())[0]['error'])
        await self.bridge.begin_login()
        self.assertIn('pending Muse', (await self.collect())[0]['error'])
        await self.bridge.cancel_login()
        self.assertIsNone(self.bridge.login)
        await self.login()
        self.assertIn('another workspace or account', (await self.collect())[0]['error'])

    async def test_workspace_context_and_api_history_scoped(self):
        self.assertIn('inside the selected workspace', (await self.collect(context_files=[str(ROOT / 'README.md')]))[0]['error'])
        memory.add_message(self.conv, 'user', 'API message')
        self.assertIn('another connection', (await self.collect())[0]['error'])
        self.conv = memory.create_conversation('Workspace test')
        await self.collect()
        other = self.root / 'other'; other.mkdir()
        preferences.save_preferences({'workspace': str(other)})
        self.assertIn('another workspace', (await self.collect())[0]['error'])

    async def test_one_time_approval_receipt_scoped_and_requirement_guarded(self):
        preferences.save_preferences({'access_mode': 'workspace-write'})
        stream = muse_chat.stream_chat('approval', self.conv)
        async for e in stream:
            if e.get('approval_required'): ticket = e['action_id']; break
        with self.assertRaises(MuseError): await self.bridge.decide('wrong', ticket, True)
        await self.bridge.decide(self.conv, ticket, False)
        with self.assertRaises(MuseError): await self.bridge.decide(self.conv, ticket, True)
        remaining = [e async for e in stream]
        self.assertEqual(remaining[-1]['response'], 'Denied')
        self.assertFalse(self.bridge.approvals)

    async def test_question_requires_complete_scoped_answers(self):
        stream = muse_chat.stream_chat('question', self.conv)
        async for e in stream:
            if e.get('question_required'): ticket = e['action_id']; break
        with self.assertRaises(MuseError): await self.bridge.answer(self.conv, ticket, {})
        with self.assertRaises(MuseError): await self.bridge.answer('wrong', ticket, {'style': 'Simple'})
        await self.bridge.answer(self.conv, ticket, {'style': 'Simple'})
        remaining = [e async for e in stream]
        self.assertEqual(remaining[-1]['response'], 'Answer received')

    async def test_stop_and_stream_cancellation_interrupt_actual_turn(self):
        stream = muse_chat.stream_chat('wait', self.conv)
        await anext(stream)
        with self.assertRaises(MuseError): await self.bridge.logout()
        self.assertIn('already has', (await self.collect())[0]['error'])
        await self.bridge.interrupt(self.conv)
        remaining = [e async for e in stream]
        self.assertIn('Task stopped', remaining[-1]['response'])
        stream = muse_chat.stream_chat('wait', self.conv)
        await anext(stream)
        with patch.object(self.bridge, 'interrupt', wraps=self.bridge.interrupt) as stop:
            await asyncio.wait_for(stream.aclose(), 3)
            stop.assert_called_once_with(self.conv)
        self.assertFalse(self.bridge.active)

    async def test_unknown_server_request_fails_closed(self):
        with patch.object(self.bridge, 'send', new_callable=AsyncMock) as send:
            await self.bridge.dispatch({'id': 'unknown', 'method': 'unknown/grantAccess'})
            self.assertIn('error', send.call_args.args[0])

    async def test_failures_and_host_death_persist_honest_errors(self):
        self.assertIn('Task failed', (await self.collect('fail'))[-1]['response'])
        events = await self.collect('crash')
        self.assertTrue(events[-1]['done'])
        self.assertTrue(any(e.get('error') for e in events))
        self.assertFalse(self.bridge.active)

    async def test_missing_sandbox_and_permission_profiles_never_relax_access(self):
        self.bridge.handshake['sandboxAvailability'] = 'unavailable'
        self.assertIn('sandbox', (await self.collect())[0]['error'])
        self.bridge.handshake['sandboxAvailability'] = 'available'
        with self.assertRaises(MuseError): muse_chat.profile_for([], 'read-only')
        self.assertFalse(memory.get_messages_full(self.conv))

    async def test_api_credentials_not_inherited(self):
        await self.bridge.close()
        with patch.dict(os.environ, {'META_API_KEY': 'secret', 'MODEL_API_KEY': 'secret', 'OPENAI_API_KEY': 'secret'}):
            self.assertTrue((await self.collect())[-1]['done'])

    async def test_http_stream_and_connection_guards(self):
        from api.server import app
        from api.routes import connections
        with patch.object(connections, 'muse_bridge', self.bridge):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8000') as client:
                self.assertEqual((await client.post('/connections/muse/login')).status_code, 403)
                headers = {'X-Asrar-Client': 'desktop-v1'}
                self.assertEqual((await client.post('/connections/muse/login', headers={**headers, 'Origin': 'https://evil.invalid'})).status_code, 403)
                self.assertEqual((await client.post('/chat/stream', json={'runtime': 'muse', 'message': 'hello'})).status_code, 403)
                response = await client.post('/chat/stream', headers=headers, json={'runtime': 'muse', 'message': 'hello', 'conversation_id': self.conv, 'muse_effort': 'max'})
                self.assertEqual(response.status_code, 200)
                self.assertIn('Hello from Muse.', response.text)
                response = await client.post('/chat/stream', headers=headers, json={'runtime': 'api', 'message': 'hello', 'conversation_id': self.conv})
                self.assertEqual(response.status_code, 409)
                self.assertNotIn('NEVER_EXPOSE', (await client.get('/connections/muse', headers=headers)).text)

    def test_trusted_login_urls(self):
        for value in ['http://auth.meta.com/x', 'https://auth.meta.com.evil.invalid/x', 'https://u:p@auth.meta.com/x', 'file:///tmp/key', 'https://auth.meta.com:invalid/x']:
            with self.assertRaises(MuseError): login_url(value)
