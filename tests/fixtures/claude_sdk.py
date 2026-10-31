"""A deterministic SDK peer. Never contacts a model or executes a command."""
import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from pathlib import Path

@dataclass
class TextBlock:
    text: str
@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict
@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False
@dataclass
class StreamEvent:
    event: dict
@dataclass
class AssistantMessage:
    content: list
@dataclass
class UserMessage:
    content: list
@dataclass
class ResultMessage:
    result: str = 'Hello Claude.'
    subtype: str = 'success'
    is_error: bool = False
    errors: list | None = None

class Options(SimpleNamespace):
    pass
class HookMatcher:
    def __init__(self, hooks, matcher=None):
        self.hooks=hooks
class PermissionResultAllow:
    def __init__(self, updated_input=None):
        self.updated_input=updated_input
class PermissionResultDeny:
    def __init__(self, message='', **kwargs):
        self.message=message

class Client:
    def __init__(self, options):
        self.options=options
        self.interrupted=False
        self.waiting=asyncio.Event()
        self.owner=None
        self.closed=False
        self.permission_result=None
        SDK.clients.append(self)
    async def __aenter__(self):
        self.owner=asyncio.current_task()
        return self
    async def __aexit__(self, *args):
        assert asyncio.current_task() is self.owner, 'SDK must close on its creating task'
        self.closed=True
    async def get_server_info(self):
        return {'models':[{'value':'claude-test','displayName':'Claude Test','supportedEffortLevels':['medium','high']}]}
    async def query(self, message):
        self.message=message
    async def interrupt(self):
        self.interrupted=True
        self.waiting.set()
    async def receive_response(self):
        if self.message == 'wait':
            await self.waiting.wait()
            return
        if self.message == 'fail':
            yield ResultMessage(result='Subscription limit reached',is_error=True)
            return
        if self.message.startswith(('approval','question','write','outside','escape')):
            name='AskUserQuestion' if self.message.startswith('question') else 'Write' if self.message.startswith(('write','outside')) else 'Bash'
            values=({'questions':[{'question':'Which approach?','header':'Approach','options':[{'label':'Simple','description':'Keep it simple'}]}]} if name=='AskUserQuestion' else
                    {'file_path':str(Path(self.options.cwd)/('example.txt' if self.message.startswith('write') else '../outside.txt')),'content':'new content\n'} if name=='Write' else
                    {'command':'rm example.txt' if self.message.startswith('approval') else 'echo harmless test', **({'dangerouslyDisableSandbox':True} if self.message.startswith('escape') else {})})
            yield AssistantMessage([ToolUseBlock('tool-test',name,values)])
            data={'tool_name':name,'tool_input':values}
            hook_result={}
            for matcher in self.options.hooks['PreToolUse']:
                for hook in matcher.hooks:
                    hook_result=await hook(data,'tool-test',{})
            if hook_result.get('hookSpecificOutput',{}).get('permissionDecision')=='deny':
                yield ResultMessage(result='Tool blocked by workspace policy')
                return
            self.permission_result=await self.options.can_use_tool(name,values,SimpleNamespace(title='Review action'))
            if isinstance(self.permission_result,PermissionResultAllow) and name=='Write':
                Path(values['file_path']).write_text(values['content'])
                for matcher in self.options.hooks['PostToolUse']:
                    for hook in matcher.hooks: await hook(data,'tool-test',{})
            yield UserMessage([ToolResultBlock('tool-test','harmless result',is_error=isinstance(self.permission_result,PermissionResultDeny))])
        yield StreamEvent({'type':'content_block_delta','delta':{'type':'text_delta','text':'Hello'}})
        yield AssistantMessage([TextBlock('Hello Claude.')])
        yield ResultMessage()

class SDK:
    clients=[]
    ClaudeAgentOptions=Options
    ClaudeSDKClient=Client
    HookMatcher=HookMatcher
    PermissionResultAllow=PermissionResultAllow
    PermissionResultDeny=PermissionResultDeny
    StreamEvent=StreamEvent
    AssistantMessage=AssistantMessage
    UserMessage=UserMessage
    ResultMessage=ResultMessage
    TextBlock=TextBlock
    ToolUseBlock=ToolUseBlock
    ToolResultBlock=ToolResultBlock
