"""Native subscription turns reuse loaded sessions without replaying history."""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from core import codex_bridge, codex_chat, memory, preferences
from core.codex_bridge import CodexBridge


class NativeTurnReuseTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bridge = CodexBridge()
        self.patches = [
            patch.object(codex_bridge, 'executable', return_value=str(ROOT / 'tests/fixtures/codex_app_server.py')),
            patch.object(codex_bridge, 'AUTH_HOME', self.root / 'auth'),
            patch.object(codex_chat, 'AUTH_HOME', self.root / 'auth'),
            patch.object(codex_chat, 'bridge', self.bridge),
            patch.object(preferences, 'PREFERENCES_PATH', self.root / 'preferences.json'),
            patch.object(memory, 'DB_PATH', self.root / 'test.db'),
        ]
        for patcher in self.patches:
            patcher.start()
        memory.init_db()
        preferences.save_preferences({'workspace': str(self.root)})
        self.conversation = memory.create_conversation('Native efficiency')

    async def asyncTearDown(self):
        await self.bridge.close()
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temp.cleanup()

    async def collect(self, message='hello'):
        return [event async for event in codex_chat.stream_chat(message, self.conversation)]

    async def test_followup_reuses_catalog_thread_and_native_history(self):
        with patch.object(self.bridge, 'request', wraps=self.bridge.request) as request:
            await self.collect()
            # Native Codex owns its history; SQLite blobs are not needed to
            # submit another turn to an already-scoped runtime conversation.
            with patch.object(memory, 'get_messages_full', side_effect=AssertionError('history replay')):
                events = await self.collect('continue')
        self.assertTrue(events[-1]['done'])
        methods = [call.args[0] for call in request.call_args_list]
        self.assertEqual(methods.count('model/list'), 1)
        self.assertEqual(methods.count('thread/start'), 1)
        self.assertEqual(methods.count('thread/resume'), 0)
        self.assertEqual(methods.count('turn/start'), 2)
        self.assertEqual(methods.count('account/read'), 2)  # auth is still fresh each turn
        self.assertEqual(len(memory.get_messages_full(self.conversation)), 4)

    async def test_changed_instructions_and_permissions_refresh_thread(self):
        await self.collect()
        preferences.save_preferences({'instructions': 'Only change Python files.', 'access_mode': 'workspace-write'})
        with patch.object(self.bridge, 'request', wraps=self.bridge.request) as request:
            await self.collect('continue')
        resume = next(call for call in request.call_args_list if call.args[0] == 'thread/resume')
        self.assertEqual(resume.args[1]['sandbox'], 'workspace-write')
        self.assertTrue(resume.args[1]['excludeTurns'])
        self.assertIn('Only change Python files.', resume.args[1]['developerInstructions'])
        turn = next(call for call in request.call_args_list if call.args[0] == 'turn/start')
        self.assertFalse(turn.args[1]['sandboxPolicy']['networkAccess'])
        self.assertEqual(turn.args[1]['approvalPolicy'], 'on-request')

    async def test_restarted_runtime_resumes_and_refreshes_catalog(self):
        await self.collect()
        await self.bridge.close()
        with patch.object(self.bridge, 'request', wraps=self.bridge.request) as request:
            events = await self.collect('continue')
        self.assertTrue(events[-1]['done'])
        methods = [call.args[0] for call in request.call_args_list]
        self.assertEqual(methods.count('model/list'), 1)
        self.assertEqual(methods.count('thread/resume'), 1)
        self.assertEqual(methods.count('thread/start'), 0)

    async def test_runtime_unload_notification_requires_resume(self):
        await self.collect()
        await self.bridge._dispatch({'method': 'thread/status/changed',
                                     'params': {'threadId': 'thread-test', 'status': {'type': 'notLoaded'}}})
        with patch.object(self.bridge, 'request', wraps=self.bridge.request) as request:
            await self.collect('continue')
        self.assertEqual(sum(call.args[0] == 'thread/resume' for call in request.call_args_list), 1)
        await self.bridge._dispatch({'method': 'thread/closed', 'params': {'threadId': 'thread-test'}})
        self.assertNotIn('thread-test', self.bridge._loaded_threads)

    async def test_catalog_cache_is_scoped_expires_and_ui_refreshes(self):
        await self.bridge.account()
        with patch.object(self.bridge, 'request', wraps=self.bridge.request) as request:
            first = await self.bridge.models(cache_scope='account-one')
            first[0]['displayName'] = 'not cached'
            second = await self.bridge.models(cache_scope='account-one')
            self.assertNotEqual(second[0]['displayName'], 'not cached')
            await self.bridge.models(cache_scope='account-two')
            self.bridge._catalog_time -= 61
            await self.bridge.models(cache_scope='account-two')
            await self.bridge.models()  # explicit refresh bypasses the task cache
        self.assertEqual(sum(call.args[0] == 'model/list' for call in request.call_args_list), 4)

    async def test_concurrent_catalog_discovery_shares_one_request(self):
        await self.bridge.account()
        with patch.object(self.bridge, 'request', wraps=self.bridge.request) as request:
            results = await asyncio.gather(*(self.bridge.models(cache_scope='same-account') for _ in range(3)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(sum(call.args[0] == 'model/list' for call in request.call_args_list), 1)


if __name__ == '__main__':
    unittest.main()
