"""Account sign-in and ACP integration, exercised without provider billing."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from core import kimi_bridge, kimi_chat, memory, preferences
from core.kimi_bridge import KimiBridge, KimiError, trusted_login_url
from core.codex_chat import session_for


class KimiConnectionsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bridge = KimiBridge()
        self.patches = [
            patch.object(kimi_bridge, 'AUTH_HOME', self.root / 'auth'),
            patch.object(kimi_bridge, 'executable', return_value=str(ROOT / 'tests/fixtures/kimi_acp.py')),
            patch.object(kimi_chat, 'bridge', self.bridge),
            patch.object(memory, 'DB_PATH', self.root / 'test.db'),
            patch.object(preferences, 'PREFERENCES_PATH', self.root / 'preferences.json'),
        ]
        for p in self.patches: p.start()
        memory.init_db()
        preferences.save_preferences({'runtime': 'kimi', 'workspace': str(self.root)})
        await self.login()
        self.conv = memory.create_conversation('Kimi test')

    async def login(self):
        await self.bridge.begin_login()
        await asyncio.wait_for(self.bridge._login_task, 3)

    async def asyncTearDown(self):
        await self.bridge.close()
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    async def collect(self, message='hello', **kwargs):
        return [event async for event in kimi_chat.stream_chat(message, self.conv, **kwargs)]

    async def test_stream_and_resume_persist_without_replaying_old_text(self):
        events = await self.collect()
        self.assertEqual(events[-1]['response'], 'Hello from Kimi.')
        self.assertEqual(session_for(self.conv)['runtime'], 'kimi')
        events = await self.collect('second turn')
        self.assertEqual(events[-1]['response'], 'Hello from Kimi.')
        self.assertNotIn('OLD REPLAY', str(events))
        self.assertEqual(len(memory.get_messages_full(self.conv)), 4)
        self.assertFalse(self.bridge.active)

    async def test_login_cancel_logout_and_no_token_exposure(self):
        status = await self.bridge.status()
        self.assertTrue(status['connected'])
        self.assertNotIn('NEVER_EXPOSE', str(status))
        await self.bridge.logout()
        self.assertFalse((await self.bridge.status())['connected'])
        await self.bridge.begin_login()
        for _ in range(50):
            if self.bridge.login.get('authUrl'): break
            await asyncio.sleep(.01)
        self.assertTrue(self.bridge.login['authUrl'].startswith('https://auth.kimi.com/'))
        self.assertIn('pending Kimi', (await self.collect())[0]['error'])
        await self.bridge.cancel_login()
        self.assertIsNone(self.bridge.login)
        self.assertIsNone(self.bridge._login_process)
        self.assertIn('Sign in', (await self.collect())[0]['error'])

    async def test_workspace_and_account_session_scoping(self):
        await self.collect()
        directory = self.root / 'other'; directory.mkdir()
        preferences.save_preferences({'workspace': str(directory)})
        self.assertIn('another workspace', (await self.collect())[0]['error'])
        preferences.save_preferences({'workspace': str(self.root)})
        await self.bridge.logout()
        await self.login()
        self.assertIn('another workspace or account', (await self.collect())[0]['error'])

    async def test_context_and_api_history_not_migrated(self):
        events = await self.collect(context_files=[str(ROOT / 'README.md')])
        self.assertIn('inside the selected workspace', events[0]['error'])
        memory.add_message(self.conv, 'user', 'API history')
        self.assertIn('another connection', (await self.collect())[0]['error'])

    async def test_approval_scoped_single_use_and_read_only_denies(self):
        events = await self.collect('approval')
        self.assertEqual(events[-1]['response'], 'Denied')
        self.assertFalse(any(e.get('approval_required') for e in events))
        preferences.save_preferences({'access_mode': 'workspace-write'})
        stream = kimi_chat.stream_chat('approval', self.conv)
        async for event in stream:
            if event.get('approval_required'):
                ticket = event['action_id']; break
        with self.assertRaises(KimiError):
            await self.bridge.decide('wrong-chat', ticket, True)
        await self.bridge.decide(self.conv, ticket, True)
        with self.assertRaises(KimiError):
            await self.bridge.decide(self.conv, ticket, True)
        remaining = [e async for e in stream]
        self.assertEqual(remaining[-1]['response'], 'Approved')
        self.assertFalse(self.bridge.approvals)

    async def test_stop_closes_runtime_and_clears_state(self):
        stream = kimi_chat.stream_chat('wait', self.conv)
        await anext(stream)
        with self.assertRaises(KimiError): await self.bridge.logout()
        self.assertIn('already has', (await self.collect())[0]['error'])
        await self.bridge.interrupt(self.conv)
        events = await asyncio.wait_for(self.finish(stream), 3)
        self.assertIn('Task stopped', events[-1]['response'])
        self.assertFalse(self.bridge.active)

    async def finish(self, stream):
        return [e async for e in stream]

    async def test_disconnected_stream_and_task_cancellation_stop_process(self):
        stream = kimi_chat.stream_chat('wait', self.conv)
        await anext(stream)
        client = self.bridge.active[self.conv]['client']
        await stream.aclose()
        self.assertIsNotNone(client.process.returncode)
        self.assertFalse(self.bridge.active)
        stream = kimi_chat.stream_chat('wait', self.conv)
        await anext(stream)
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(.02)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError): await pending
        self.assertFalse(self.bridge.active)

    async def test_failures_and_invalid_model_do_not_expose_payloads(self):
        events = await self.collect(model='bad-model')
        self.assertTrue(events[-1]['done'])
        self.assertNotIn('NEVER_EXPOSE', str(events))
        self.assertFalse(memory.get_messages_full(self.conv))
        events = await self.collect('fail')
        self.assertIn('Task failed', events[-1]['response'])
        self.assertNotIn('NEVER_EXPOSE', str(events))

    async def test_api_credentials_not_inherited(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'secret', 'KIMI_API_KEY': 'secret', 'MOONSHOT_API_KEY': 'secret'}):
            self.assertEqual((await self.collect())[-1]['response'], 'Hello from Kimi.')

    async def test_local_client_and_cross_runtime_guards(self):
        from api.server import app
        from api.routes import connections
        with patch.object(connections, 'kimi_bridge', self.bridge):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8000') as client:
                self.assertEqual((await client.post('/connections/kimi/login')).status_code, 403)
                headers = {'X-Asrar-Client': 'desktop-v1'}
                self.assertEqual((await client.post('/connections/kimi/login', headers={**headers, 'Origin': 'https://evil.invalid'})).status_code, 403)
                self.assertEqual((await client.post('/chat/stream', json={'runtime': 'kimi', 'message': 'hello'})).status_code, 403)
                await self.collect()
                response = await client.post('/chat/stream', headers=headers, json={'runtime': 'api', 'message': 'hello', 'conversation_id': self.conv})
                self.assertEqual(response.status_code, 409)
                status = await client.get('/connections/kimi', headers=headers)
                self.assertTrue(status.json()['connected'])
                self.assertNotIn('NEVER_EXPOSE', status.text)
                response = await client.post('/chat/stream', headers=headers, json={'runtime': 'kimi', 'message': 'hello'})
                self.assertEqual(response.status_code, 200)
                self.assertIn('Hello from Kimi.', response.text)
                self.assertIn('"done": true', response.text)

    def test_urls_reject_untrusted_hosts_and_credentials(self):
        for value in ['http://auth.kimi.com/x', 'https://auth.kimi.com.evil.invalid/x', 'https://user:pass@auth.kimi.com/x', 'file:///tmp/key', 'https://auth.kimi.com:invalid/x']:
            with self.assertRaises(KimiError): trusted_login_url(value)
