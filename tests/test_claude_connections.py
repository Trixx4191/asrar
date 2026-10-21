"""Claude subscription orchestration, using fake CLI/SDK peers without billing."""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from core import claude_bridge, claude_chat, memory, preferences
from core.codex_chat import save_session, session_for
from core.claude_bridge import ClaudeBridge, ClaudeError
from fixtures.claude_sdk import SDK, PermissionResultAllow, PermissionResultDeny


class ClaudeSubscriptionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.bridge=ClaudeBridge()
        self.patches=[
            patch.object(claude_bridge,'AUTH_HOME',self.root/'auth'),
            patch.object(claude_chat,'AUTH_HOME',self.root/'auth'),
            patch.object(claude_chat,'bridge',self.bridge),
            patch.object(claude_bridge,'executable',return_value=str(ROOT/'tests/fixtures/claude_auth_cli.py')),
            patch.object(claude_bridge,'sdk_types',return_value=SDK),
            patch.object(claude_chat,'sdk_types',return_value=SDK),
            patch.object(memory,'DB_PATH',self.root/'test.db'),
            patch.object(preferences,'PREFERENCES_PATH',self.root/'preferences.json'),
        ]
        for patcher in self.patches: patcher.start()
        memory.init_db()
        preferences.save_preferences({'runtime':'claude','workspace':str(self.root)})
        self.conv=memory.create_conversation('Claude test')
        SDK.clients=[]
        (self.root/'auth').mkdir()
        self.account_file=self.root/'auth/fake-account.json'
        self.sign_in()

    def sign_in(self):
        self.account_file.write_text(json.dumps({'loggedIn':True,'authMethod':'claude.ai','email':'test@example.invalid','subscriptionType':'pro','accessToken':'NEVER_EXPOSE'}))

    async def asyncTearDown(self):
        await self.bridge.close()
        for patcher in reversed(self.patches): patcher.stop()
        self.temp.cleanup()

    async def collect(self,message='hello',**kwargs):
        return [event async for event in claude_chat.stream_chat(message,self.conv,**kwargs)]

    async def test_auth_allowlist_and_api_credentials_isolated(self):
        account=await self.bridge.account()
        self.assertEqual(account,{'type':'claude.ai','email':'test@example.invalid','plan':'pro'})
        with patch.dict(os.environ,{'ANTHROPIC_API_KEY':'secret','ANTHROPIC_AUTH_TOKEN':'secret','CLAUDE_CODE_USE_BEDROCK':'1','CLAUDE_CODE_OAUTH_TOKEN':'secret'}):
            clean=claude_bridge.cli_environment()
            overrides=claude_bridge.sdk_environment()
            for key in ('ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN','CLAUDE_CODE_OAUTH_TOKEN','CLAUDE_CODE_USE_BEDROCK'):
                self.assertNotIn(key,clean)
                self.assertEqual(overrides[key],'')
        self.assertEqual(clean['CLAUDE_CONFIG_DIR'],str(self.root/'auth'))

    async def test_rejects_api_or_console_account(self):
        self.account_file.write_text(json.dumps({'loggedIn':True,'authMethod':'api_key'}))
        with self.assertRaises(ClaudeError): await self.bridge.account()
        events=await self.collect()
        self.assertIn('requires Claude subscription',events[0]['error'])
        self.assertEqual(memory.get_messages_full(self.conv),[])

    async def test_stream_final_text_saved_and_read_only_tools(self):
        events=await self.collect()
        final=next(event for event in events if event.get('done'))
        self.assertEqual(final['response'],'Hello Claude.')
        self.assertEqual(memory.get_messages_full(self.conv)[-1]['content'],'Hello Claude.')
        self.assertEqual(session_for(self.conv)['runtime'],'claude')
        client=SDK.clients[-1]
        self.assertNotIn('Bash',client.options.tools)
        self.assertNotIn('Edit',client.options.tools)
        self.assertNotIn('WebFetch',client.options.tools)
        self.assertTrue(client.closed)
        self.assertEqual(client.options.settings,'{"forceLoginMethod":"claudeai"}')
        self.assertFalse(self.bridge.active)

    async def test_resume_and_workspace_or_account_changes(self):
        await self.collect()
        session=session_for(self.conv)
        await self.collect('second')
        self.assertEqual(SDK.clients[-1].options.resume,session['thread_id'])
        (self.root/'auth/asrar-session-id').write_text('another-login')
        events=await self.collect()
        self.assertIn('another workspace or Claude account',events[0]['error'])

    async def test_codex_chat_cannot_be_used_with_claude(self):
        save_session(self.conv,'codex-thread',str(self.root),'scope')
        events=await self.collect()
        self.assertIn('another connection',events[0]['error'])

    async def test_api_history_not_silently_transferred(self):
        memory.add_message(self.conv,'user','API conversation')
        events=await self.collect()
        self.assertIn('another connection',events[0]['error'])

    async def test_unknown_model_or_effort_rejected(self):
        self.assertIn('unavailable',(await self.collect(model='made-up'))[0]['error'])
        self.assertIn('supported Claude effort',(await self.collect(model='claude-test',effort='imaginary'))[0]['error'])
        self.assertEqual(memory.get_messages_full(self.conv),[])

    async def test_approval_scoped_and_single_use(self):
        preferences.save_preferences({'access_mode':'workspace-write'})
        stream=claude_chat.stream_chat('approval',self.conv)
        async for event in stream:
            if event.get('approval_required'):
                ticket=event['action_id']; break
        with self.assertRaises(ClaudeError): await self.bridge.decide('wrong',ticket,True)
        await self.bridge.decide(self.conv,ticket,False)
        with self.assertRaises(ClaudeError): await self.bridge.decide(self.conv,ticket,True)
        remaining=[event async for event in stream]
        self.assertTrue(any(event.get('done') for event in remaining))
        self.assertIsInstance(SDK.clients[-1].permission_result,PermissionResultDeny)

    async def test_question_answers_map_to_original_question(self):
        stream=claude_chat.stream_chat('question',self.conv)
        async for event in stream:
            if event.get('question_required'):
                ticket=event['action_id']; break
        with self.assertRaises(ClaudeError): await self.bridge.answer(self.conv,ticket,{})
        await self.bridge.answer(self.conv,ticket,{'0':'Simple'})
        remaining=[event async for event in stream]
        self.assertTrue(any(event.get('done') for event in remaining))
        result=SDK.clients[-1].permission_result
        self.assertEqual(result.updated_input['answers'],{'Which approach?':'Simple'})

    async def test_write_diff_and_sandbox_fail_closed_configuration(self):
        preferences.save_preferences({'access_mode':'workspace-write'})
        stream=claude_chat.stream_chat('write',self.conv)
        async for event in stream:
            if event.get('approval_required'):
                await self.bridge.decide(self.conv,event['action_id'],True)
                break
        remaining=[event async for event in stream]
        self.assertTrue(any(event.get('diff') for event in remaining))
        self.assertEqual((self.root/'example.txt').read_text(),'new content\n')
        sandbox=SDK.clients[-1].options.sandbox
        self.assertTrue(sandbox['failIfUnavailable'])
        self.assertFalse(sandbox['allowUnsandboxedCommands'])
        self.assertEqual(sandbox['network']['deniedDomains'],['*'])

    async def test_navigation_hook_allows_cd_but_still_asks_for_writes(self):
        preferences.save_preferences({'access_mode':'workspace-write'})
        await self.collect()
        hook = SDK.clients[-1].options.hooks['PreToolUse'][0].hooks[0]
        for command, expected in [('cd ..', 'allow'), ('cd /tmp && pwd', 'allow'),
                                  ('cd /tmp && touch file', 'ask'), ('chmod 777 file', 'ask')]:
            result = await hook({'tool_name':'Bash', 'tool_input':{'command':command}}, command, {})
            self.assertEqual(result['hookSpecificOutput']['permissionDecision'], expected)

    async def test_outside_workspace_and_sandbox_escape_blocked(self):
        preferences.save_preferences({'access_mode':'workspace-write'})
        events=await self.collect('outside')
        self.assertFalse(any(event.get('approval_required') for event in events))
        self.assertEqual(next(event for event in events if event.get('done'))['response'],'Tool blocked by workspace policy')
        other=memory.create_conversation('escape')
        events=[event async for event in claude_chat.stream_chat('escape',other)]
        self.assertEqual(next(event for event in events if event.get('done'))['response'],'Tool blocked by workspace policy')

    async def test_stop_interrupts_and_sdk_closes_on_owner_task(self):
        stream=claude_chat.stream_chat('wait',self.conv)
        await anext(stream)
        for _ in range(50):
            if self.bridge.active[self.conv].get('client'): break
            await asyncio.sleep(.01)
        client=SDK.clients[-1]
        await self.bridge.interrupt(self.conv)
        await stream.aclose()
        self.assertTrue(client.interrupted)
        self.assertTrue(client.closed)
        self.assertFalse(self.bridge.active)

    async def test_logout_and_second_task_blocked_while_active(self):
        stream=claude_chat.stream_chat('wait',self.conv)
        await anext(stream)
        with self.assertRaises(ClaudeError): await self.bridge.logout()
        events=await self.collect()
        self.assertIn('already has',events[0]['error'])
        await stream.aclose()

    async def test_stop_before_worker_starts_still_completes_stream(self):
        stream=claude_chat.stream_chat('wait',self.conv)
        await anext(stream)
        await self.bridge.interrupt(self.conv)
        async def finish():
            return [event async for event in stream]
        remaining=await asyncio.wait_for(finish(),3)
        self.assertTrue(any(event.get('done') and 'Task stopped' in event['response'] for event in remaining))
        self.assertFalse(self.bridge.active)

    async def test_http_task_cancellation_closes_runtime(self):
        stream=claude_chat.stream_chat('wait',self.conv)
        await anext(stream)
        pending=asyncio.create_task(anext(stream))
        await asyncio.sleep(.03)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError): await pending
        self.assertTrue(SDK.clients[-1].closed)
        self.assertFalse(self.bridge.active)

    async def test_context_paths_and_workspace_changes_are_enforced(self):
        target=self.root/'context.txt'; target.write_text('context')
        self.assertTrue(any(event.get('done') for event in await self.collect(context_files=['context.txt'])))
        self.assertIn('context.txt',memory.get_messages_full(self.conv)[0]['content'])
        other=self.root/'other'; other.mkdir()
        preferences.save_preferences({'workspace':str(other)})
        self.assertIn('another workspace',(await self.collect())[0]['error'])
        conv=memory.create_conversation('outside attachment')
        events=[event async for event in claude_chat.stream_chat('hello',conv,context_files=[str(target)])]
        self.assertIn('existing workspace file',events[0]['error'])

    async def test_failure_persisted_and_worker_initialization_failure_ends_stream(self):
        events=await self.collect('fail')
        self.assertTrue(any(event.get('error')=='Subscription limit reached' for event in events))
        self.assertIn('Task failed',memory.get_messages_full(self.conv)[-1]['content'])
        other=memory.create_conversation('broken SDK')
        with patch.object(claude_chat,'sdk_types',side_effect=ClaudeError('SDK unavailable')):
            events=await asyncio.wait_for(self._collect_other(other),3)
        self.assertTrue(any(event.get('error')=='SDK unavailable' for event in events))

    async def _collect_other(self,conv):
        return [event async for event in claude_chat.stream_chat('hello',conv)]

    async def test_auth_login_code_cancel_logout_lifecycle(self):
        await self.bridge.logout()
        self.assertIsNone(await self.bridge.account())
        await self.bridge.begin_login()
        for _ in range(50):
            if self.bridge.login.get('authUrl'): break
            await asyncio.sleep(.02)
        self.assertTrue(self.bridge.login['authUrl'].startswith('https://claude.ai/'))
        events=await self.collect()
        self.assertIn('pending Claude sign-in',events[0]['error'])
        await self.bridge.submit_code('verification-test')
        await asyncio.wait_for(self.bridge._login_task,3)
        self.assertIsNone(self.bridge.login)
        self.assertIsNotNone(await self.bridge.account())
        self.assertIsNone(self.bridge.login_error)
        await self.bridge.logout()
        await self.bridge.begin_login()
        await self.bridge.cancel_login()
        self.assertIsNone(self.bridge.login)
        self.assertIsNone(self.bridge._login_process)

    async def test_claude_endpoints_require_local_client_and_correct_runtime(self):
        from api.server import app
        from api.routes import connections
        with patch.object(connections,'claude_bridge',self.bridge):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8000') as client:
                response=await client.post('/connections/claude/login')
                self.assertEqual(response.status_code,403)
                headers={'X-Asrar-Client':'desktop-v1'}
                response=await client.post('/connections/claude/login',headers={**headers,'Origin':'https://evil.invalid'})
                self.assertEqual(response.status_code,403)
                response=await client.post('/chat/stream',json={'runtime':'claude','message':'hello'})
                self.assertEqual(response.status_code,403)
                await self.collect()
                response=await client.post('/chat/stream',json={'runtime':'codex','message':'next','conversation_id':self.conv},headers=headers)
                self.assertEqual(response.status_code,409)
                response=await client.get('/conversations/'+self.conv)
                self.assertEqual(response.json()['conversation']['runtime'],'claude')


class ClaudeUrlTest(unittest.TestCase):
    def test_auth_urls_only_allow_official_secure_hosts(self):
        for value in ['file:///tmp/key','http://claude.ai/login','https://claude.ai.evil.invalid/login','https://user:pass@claude.ai/login']:
            with self.assertRaises(ClaudeError): claude_bridge.trusted_login_url(value)
        self.assertEqual(claude_bridge.trusted_login_url('https://claude.ai/oauth/authorize'),'https://claude.ai/oauth/authorize')
