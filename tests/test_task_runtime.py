"""Exercise task-sized menus, native discovery, accounting, and context isolation."""
import asyncio
from contextlib import ExitStack
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from core import agent, context, orchestrator, runtime
from core.tool_catalog import ToolCatalog, GROUPS
from providers.base import ProviderResponse


class CatalogTests(unittest.TestCase):
    def test_chat_menu_is_small_but_all_capabilities_can_be_discovered(self):
        menu = ToolCatalog(agent.TOOL_SCHEMAS)
        menu.configure('fast_chat')
        self.assertEqual({s['function']['name'] for s in menu.schemas}, {'use_skill', 'discover_tools'})
        self.assertLess(len(json.dumps(menu.schemas)), len(json.dumps(agent.TOOL_SCHEMAS)) / 4)
        menu.discover('all')
        self.assertEqual(menu.schemas, agent.TOOL_SCHEMAS)

    def test_discovery_is_local_idempotent_and_rejects_invalid_groups(self):
        first, second = ToolCatalog(agent.TOOL_SCHEMAS), ToolCatalog(agent.TOOL_SCHEMAS)
        first.configure('fast_chat')
        first.discover('web')
        self.assertTrue(GROUPS['web'] <= first.enabled)
        self.assertFalse(GROUPS['web'] & second.enabled)
        self.assertEqual(first.discover('web'), first.discover('web'))
        before = first.schemas
        self.assertTrue(first.discover('does-not-exist').startswith('Error:'))
        self.assertEqual(before, first.schemas)


class TaskRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setup_agent(self, provider, task='general'):
        model = {'id': 'test', 'provider': 'test', 'display_name': 'Test'}
        decision = SimpleNamespace(selected_model_key='test', selected_model=model,
                                   fallback_chain=[], reason='test', task_type=task)
        stack = ExitStack()
        stack.enter_context(patch.object(agent, 'route_async', AsyncMock(return_value=decision)))
        stack.enter_context(patch.object(agent, 'load_registry', return_value={'models': {'test': model}}))
        stack.enter_context(patch.object(agent, 'get_provider', return_value=provider))
        stack.enter_context(patch.object(agent, '_log'))
        stack.enter_context(patch.object(orchestrator, 'is_available', return_value=True))
        stack.enter_context(patch.object(orchestrator, 'record_success'))
        return stack

    async def test_discovered_tools_reach_next_buffered_and_streaming_request(self):
        for streamed in [False, True]:
            with self.subTest(streamed=streamed):
                requests = []
                async def complete(**kwargs):
                    requests.append(kwargs)
                    if len(requests) == 1:
                        return ProviderResponse('', 'test', 'test', input_tokens=30, output_tokens=10, tool_calls=[
                            {'id': 'discover', 'name': 'discover_tools', 'args': {'category': 'web'}}])
                    return ProviderResponse('Ready to search.', 'test', 'test', input_tokens=50, output_tokens=8)
                provider = SimpleNamespace(is_available=lambda: True, complete=complete)
                with self.setup_agent(provider, 'fast_chat'):
                    if streamed:
                        result = [event async for event in agent.run_task_stream('hello')][-1]
                    else:
                        result = await agent.run_task('hello')
                initial = {s['function']['name'] for s in requests[0]['tools']}
                later = {s['function']['name'] for s in requests[1]['tools']}
                self.assertNotIn('web_search', initial)
                self.assertIn('web_search', later)
                self.assertEqual(result['usage']['model_requests'], 2)
                self.assertEqual(result['usage']['input_tokens'], 80)
                self.assertEqual(result['usage']['output_tokens'], 18)
                self.assertEqual(result['usage']['reported_usage_requests'], 2)
                self.assertEqual(result['response'], 'Ready to search.')
                self.assertIsNone(runtime.current())

    async def test_true_streaming_also_accounts_for_usage_and_scoped_tools(self):
        requests = []
        async def stream_complete(**kwargs):
            requests.append(kwargs)
            yield {'type': 'token', 'text': 'Hello.'}
            yield {'type': 'done', 'response': ProviderResponse('Hello.', 'test', 'test', input_tokens=100, output_tokens=2)}
        provider = SimpleNamespace(is_available=lambda: True, stream_complete=stream_complete)
        with self.setup_agent(provider, 'fast_chat'):
            events = [event async for event in agent.run_task_stream('hello')]
        self.assertEqual(events[-1]['usage']['model_requests'], 1)
        self.assertEqual(events[-1]['usage']['input_tokens'], 100)
        self.assertEqual(len(requests[0]['tools']), 2)
        self.assertTrue(any(event.get('token') == 'Hello.' for event in events))

    async def test_tool_output_is_bounded_inside_both_task_paths(self):
        for streamed in [False, True]:
            requests = []
            async def complete(**kwargs):
                requests.append(kwargs)
                if len(requests) == 1:
                    return ProviderResponse('', 'test', 'test', tool_calls=[
                        {'id': 'read', 'name': 'read_file', 'args': {'path': 'large.log'}}])
                return ProviderResponse('Checked.', 'test', 'test')
            provider = SimpleNamespace(is_available=lambda: True, complete=complete)
            with self.subTest(streamed=streamed), self.setup_agent(provider, 'coding'), \
                 patch.object(agent, '_call_tool', AsyncMock(return_value='START\n' + 'line\n' * 30000 + 'END-CHECK')):
                result = ([event async for event in agent.run_task_stream('inspect the log')][-1]
                          if streamed else await agent.run_task('inspect the log'))
            tool_message = next(m for m in requests[1]['messages'] if m.role == 'tool')
            self.assertLessEqual(len(tool_message.content), context.MAX_RECENT_TOOL_CHARS)
            self.assertIn('START', tool_message.content)
            self.assertIn('END-CHECK', tool_message.content)
            self.assertGreater(result['usage']['context_chars_saved'], 100000)
            self.assertEqual(result['usage']['model_requests'], 2)

    async def test_concurrent_scopes_do_not_share_discovery_or_counters(self):
        async def task(category):
            with runtime.task_scope(agent.TOOL_SCHEMAS) as state:
                state.catalog.configure('fast_chat')
                state.catalog.discover(category)
                await asyncio.sleep(0)
                state.prepare([{'role': 'user', 'content': category}], 'system')
                return set(state.catalog.enabled), state.summary()
        (web, first), (git, second) = await asyncio.gather(task('web'), task('git'))
        self.assertNotIn('git_commit', web)
        self.assertNotIn('web_search', git)
        self.assertEqual(first['model_requests'], 1)
        self.assertEqual(second['model_requests'], 1)
        self.assertIsNone(runtime.current())

    async def test_failed_request_counts_attempts_and_does_not_leak_scope(self):
        provider = SimpleNamespace(complete=AsyncMock(side_effect=RuntimeError('offline')))
        with runtime.task_scope(agent.TOOL_SCHEMAS) as state:
            state.catalog.configure('coding')
            text, calls = await agent._llm_call_with_tools(provider, 'test', [{'role': 'user', 'content': 'fix'}], 'system', 'test')
            self.assertEqual(state.summary()['model_requests'], 1)
            self.assertIn('Provider error', text)
            self.assertEqual(calls, [])
        self.assertIsNone(runtime.current())


if __name__ == '__main__':
    unittest.main()
