"""Connection reuse is lifespan-bound; provider usage survives SSE-only frames."""
import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from providers import transport
from providers.base import Message
from providers.openai_compat import openai_complete, openai_stream
from providers.anthropic import AnthropicProvider
from providers.google import GoogleProvider


class PausedBody(httpx.AsyncByteStream):
    def __init__(self):
        self.closed = False

    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
        await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


def sse(events):
    return ''.join('data: ' + (event if isinstance(event, str) else json.dumps(event)) + '\n\n' for event in events)


class ProviderTransportTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.created = []
        self.requests = []
        self.response = lambda request: httpx.Response(200, json={
            'choices': [{'message': {'content': 'hello'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 11, 'completion_tokens': 3},
        })
        real_client = httpx.AsyncClient

        def handler(request):
            self.requests.append(request)
            return self.response(request)

        def client(**kwargs):
            instance = real_client(transport=httpx.MockTransport(handler), **kwargs)
            self.created.append(instance)
            return instance

        self.patcher = patch.object(transport.httpx, 'AsyncClient', side_effect=client)
        self.patcher.start()

    async def asyncTearDown(self):
        self.patcher.stop()
        self.assertTrue(all(client.is_closed for client in self.created))
        self.assertNotIn(asyncio.get_running_loop(), transport._client_pools)

    def arguments(self, **changes):
        return dict(base_url='https://provider.invalid/v1', headers={'Authorization': 'Bearer first'},
                    model_id='test', messages=[Message('user', 'hello')], system=None,
                    max_tokens=32, tools=None, temperature=None, provider_name='test', **changes)

    async def test_agent_rounds_share_client_until_lifespan_exit(self):
        async with transport.provider_client_pool():
            first = await openai_complete(**self.arguments())
            second = await openai_complete(**self.arguments())
            self.assertTrue(first.success and second.success)
            self.assertEqual(len(self.created), 1)
            self.assertFalse(self.created[0].is_closed)
            self.assertNotIn('authorization', self.created[0].headers)
        self.assertTrue(self.created[0].is_closed)
        self.assertEqual([r.headers['Authorization'] for r in self.requests], ['Bearer first', 'Bearer first'])

    async def test_keys_and_endpoints_never_share_cookie_or_auth_state(self):
        self.response = lambda request: httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]},
                                                       headers={'set-cookie': 'session=private; Path=/'})
        async with transport.provider_client_pool():
            await openai_complete(**self.arguments())
            args = self.arguments()
            args['headers'] = {'Authorization': 'Bearer second'}
            await openai_complete(**args)
            args['base_url'] = 'https://other.invalid/v1'
            await openai_complete(**args)
            self.assertEqual(len(self.created), 3)
            self.assertTrue(all('authorization' not in client.headers for client in self.created))
        self.assertTrue(all('cookie' not in request.headers for request in self.requests))
        self.assertEqual([r.headers['Authorization'] for r in self.requests], ['Bearer first', 'Bearer second', 'Bearer second'])

    async def test_standalone_calls_close_clients_without_global_pool(self):
        await openai_complete(**self.arguments())
        await openai_complete(**self.arguments())
        self.assertEqual(len(self.created), 2)
        self.assertTrue(all(client.is_closed for client in self.created))

    async def test_nested_pool_does_not_close_outer_clients_and_errors_cleanup(self):
        with self.assertRaisesRegex(RuntimeError, 'test failure'):
            async with transport.provider_client_pool():
                await openai_complete(**self.arguments())
                async with transport.provider_client_pool():
                    await openai_complete(**self.arguments())
                self.assertEqual(len(self.created), 1)
                self.assertFalse(self.created[0].is_closed)
                raise RuntimeError('test failure')
        self.assertTrue(self.created[0].is_closed)

    async def test_cancelling_stream_closes_response_but_preserves_pooled_client(self):
        body = PausedBody()
        self.response = lambda request: httpx.Response(200, stream=body)
        async with transport.provider_client_pool():
            stream = openai_stream(**self.arguments())
            self.assertEqual((await anext(stream))['text'], 'hello')
            waiting = asyncio.create_task(anext(stream))
            await asyncio.sleep(0)
            waiting.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiting
            self.assertTrue(body.closed)
            self.assertFalse(self.created[0].is_closed)
            self.response = lambda request: httpx.Response(200, json={'choices': [{'message': {'content': 'still usable'}}]})
            self.assertTrue((await openai_complete(**self.arguments())).success)
            self.assertEqual(len(self.created), 1)

    async def test_server_lifespan_owns_transport_pool(self):
        from api import server
        with patch.object(server.bridge, 'close', new_callable=AsyncMock), \
             patch.object(server.claude_bridge, 'close', new_callable=AsyncMock), \
             patch.object(server.kimi_bridge, 'close', new_callable=AsyncMock), \
             patch.object(server.muse_bridge, 'close', new_callable=AsyncMock):
            async with server.lifespan(server.app):
                await openai_complete(**self.arguments())
                await openai_complete(**self.arguments())
                self.assertEqual(len(self.created), 1)
                self.assertFalse(self.created[0].is_closed)
        self.assertTrue(self.created[0].is_closed)

    async def test_openai_usage_only_frames_preserved_without_extra_options(self):
        self.response = lambda request: httpx.Response(200, text=sse([
            {'choices': [{'delta': {'content': 'hello'}, 'finish_reason': 'stop'}]},
            {'choices': [], 'usage': {'prompt_tokens': 91, 'completion_tokens': 7}}, '[DONE]',
        ]))
        events = [event async for event in openai_stream(**self.arguments())]
        result = events[-1]['response']
        self.assertTrue(result.success)
        self.assertEqual((result.input_tokens, result.output_tokens), (91, 7))
        self.assertNotIn('stream_options', json.loads(self.requests[0].content))

    async def test_groq_usage_metadata_is_preserved(self):
        self.response = lambda request: httpx.Response(200, text=sse([
            {'choices': [{'delta': {'content': 'hello'}, 'finish_reason': 'stop'}],
             'x_groq': {'usage': {'prompt_tokens': 23, 'completion_tokens': 4}}}, '[DONE]',
        ]))
        events = [event async for event in openai_stream(**self.arguments())]
        self.assertEqual((events[-1]['response'].input_tokens, events[-1]['response'].output_tokens), (23, 4))

    async def test_anthropic_usage_is_cumulative_not_summed(self):
        self.response = lambda request: httpx.Response(200, text=sse([
            {'type': 'message_start', 'message': {'usage': {'input_tokens': 19, 'output_tokens': 1}}},
            {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'hello'}},
            {'type': 'message_delta', 'usage': {'output_tokens': 3}, 'delta': {}},
            {'type': 'message_delta', 'usage': {'output_tokens': 5}, 'delta': {'stop_reason': 'end_turn'}},
            {'type': 'message_stop'},
        ]))
        async with transport.provider_client_pool():
            provider = AnthropicProvider()
            provider.api_key = 'anthropic-test'
            events = [event async for event in provider.stream_complete([Message('user', 'hello')], 'test')]
        result = events[-1]['response']
        self.assertEqual((result.input_tokens, result.output_tokens, result.finish_reason), (19, 5, 'end_turn'))
        self.assertEqual(self.requests[0].headers['x-api-key'], 'anthropic-test')

    async def test_google_stream_usage_frame_and_completion_usage_include_reasoning(self):
        usage = {'promptTokenCount': 17, 'toolUsePromptTokenCount': 2, 'candidatesTokenCount': 5, 'thoughtsTokenCount': 3}
        self.response = lambda request: httpx.Response(200, text=sse([
            {'candidates': [{'content': {'parts': [{'text': 'hello'}]}, 'finishReason': 'STOP'}]},
            {'usageMetadata': usage},
        ]))
        async with transport.provider_client_pool():
            provider = GoogleProvider()
            provider.api_key = 'google-test'
            events = [event async for event in provider.stream_complete([Message('user', 'hello')], 'test')]
            self.response = lambda request: httpx.Response(200, json={
                'candidates': [{'content': {'parts': [{'text': 'hello'}]}, 'finishReason': 'STOP'}], 'usageMetadata': usage,
            })
            complete = await provider.complete([Message('user', 'hello')], 'test')
            self.assertEqual(len(self.created), 1)
        for result in (events[-1]['response'], complete):
            self.assertEqual((result.input_tokens, result.output_tokens, result.finish_reason), (19, 8, 'STOP'))
        self.assertEqual(self.requests[0].headers['x-goog-api-key'], 'google-test')


class SeparateLoopTest(unittest.TestCase):
    def test_pool_never_reuses_clients_across_event_loops(self):
        real_client = httpx.AsyncClient
        created = []
        def client(**kwargs):
            instance = real_client(transport=httpx.MockTransport(lambda request: httpx.Response(200)), **kwargs)
            created.append(instance)
            return instance
        async def run():
            async with transport.provider_client_pool():
                async with transport.provider_client('test', 'https://provider.invalid', {'Authorization': 'Bearer test'}) as connection:
                    await connection.get('https://provider.invalid', headers={'Authorization': 'Bearer test'})
        with patch.object(transport.httpx, 'AsyncClient', side_effect=client):
            asyncio.run(run())
            asyncio.run(run())
        self.assertEqual(len(created), 2)
        self.assertIsNot(created[0], created[1])
        self.assertTrue(all(client.is_closed for client in created))
        self.assertFalse(transport._client_pools)


if __name__ == '__main__':
    unittest.main()
