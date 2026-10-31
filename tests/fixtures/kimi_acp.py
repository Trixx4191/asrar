#!/usr/bin/env python3
"""Deterministic Kimi peer; no network, model requests, or real credentials."""
import json
import os
from pathlib import Path
import sys
import time

home = Path(os.environ['KIMI_SHARE_DIR'])
if 'login' in sys.argv:
    print('Verification URL: https://auth.kimi.com/device?user_code=TEST', flush=True)
    time.sleep(.15)
    (home / 'credentials').mkdir(exist_ok=True)
    (home / 'credentials/kimi-code.json').write_text(json.dumps({'access_token': 'NEVER_EXPOSE', 'refresh_token': 'NEVER_EXPOSE'}))
    raise SystemExit(0)


def send(data):
    print(json.dumps({'jsonrpc': '2.0', **data}), flush=True)


def reply(req, result):
    send({'id': req['id'], 'result': result})


pending = None
for line in sys.stdin:
    req = json.loads(line)
    method = req.get('method')
    if method == 'initialize':
        assert not any(k.endswith('_API_KEY') for k in os.environ)
        reply(req, {'protocolVersion': 1, 'agentCapabilities': {'loadSession': True}})
    elif method == 'session/new':
        reply(req, {'sessionId': 'kimi-test', 'models': {'availableModels': [{'modelId': 'kimi-code', 'name': 'Kimi Code'}], 'currentModelId': 'kimi-code'}})
    elif method == 'session/load':
        send({'method': 'session/update', 'params': {'sessionId': 'kimi-test', 'update': {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': 'OLD REPLAY'}}}})
        reply(req, {})
    elif method == 'session/set_model':
        if req['params']['modelId'] == 'bad-model':
            send({'id': req['id'], 'error': {'code': -32602, 'message': 'NEVER_EXPOSE'}})
        else:
            reply(req, {})
    elif method == 'session/prompt':
        text = req['params']['prompt'][0]['text']
        if text.endswith('wait'):
            continue
        if text.endswith('fail'):
            send({'id': req['id'], 'error': {'code': -32001, 'message': 'NEVER_EXPOSE'}})
            continue
        if text.endswith(('approval', 'read permission', 'search permission')):
            pending = req
            send({'id': 'permission-test', 'method': 'session/request_permission', 'params': {'sessionId': 'kimi-test', 'toolCall': {'title': 'Edit file', 'kind': 'read' if text.endswith('read permission') else 'search' if text.endswith('search permission') else 'edit'}, 'options': [{'optionId': 'allow', 'kind': 'allow_once'}, {'optionId': 'deny', 'kind': 'reject_once'}]}})
            continue
        send({'method': 'session/update', 'params': {'sessionId': 'kimi-test', 'update': {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': 'Hello from Kimi.'}}}})
        reply(req, {'stopReason': 'end_turn'})
    elif req.get('id') == 'permission-test' and pending:
        outcome = req['result']['outcome']
        answer = 'Approved' if outcome.get('optionId') == 'allow' else 'Denied'
        send({'method': 'session/update', 'params': {'sessionId': 'kimi-test', 'update': {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': answer}}}})
        reply(pending, {'stopReason': 'end_turn'})
        pending = None
