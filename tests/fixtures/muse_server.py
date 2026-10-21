#!/usr/bin/env python3
"""Offline Muse Session Protocol peer with stateful login and staged approvals."""
import json
import os
from pathlib import Path
import sys
import uuid

home = Path(os.environ['XDG_CONFIG_HOME']).parent
account = home / 'fake-account'
session = None
turn = None
permission = None
ready = False


def send(data):
    print(json.dumps({'jsonrpc': '2.0', **data}), flush=True)


def notify(method, params):
    send({'method': method, 'params': params})


def reply(req, value):
    send({'id': req['id'], 'result': value})


def done(text='Hello from Muse.', terminal='completed'):
    if text:
        notify('item/started', {'sessionId': session, 'item': {'itemId': 'answer', 'kind': 'agentMessage', 'revision': 1, 'status': 'inProgress'}})
        notify('item/delta', {'sessionId': session, 'itemId': 'answer', 'delta': 'non-authoritative preview'})
        notify('item/completed', {'sessionId': session, 'item': {'itemId': 'answer', 'kind': 'agentMessage', 'revision': 2, 'status': 'completed', 'messagePhase': 'finalAnswer', 'text': text}})
    notify('turn/completed', {'sessionId': session, 'turnId': turn, 'terminal': terminal})


for line in sys.stdin:
    req = json.loads(line)
    method = req.get('method')
    p = req.get('params') or {}
    if method in {'account/read', 'account/logout', 'account/loginCancel'}:
        assert 'params' not in req
    if not method: continue  # presentation receipt
    if method == 'initialized':
        assert 'params' not in req
        ready = True
        continue
    if method != 'initialize': assert ready
    assert req.get('jsonrpc') == '2.0'
    if method == 'initialize':
        assert not any(k.endswith('_API_KEY') for k in os.environ)
        assert p['capabilities']['experimentalApi']
        reply(req, {'schema': {'version': 1}, 'sandboxAvailability': 'available', 'museHome': str(home)})
    elif method == 'account/read':
        reply(req, {'state': 'accountLogin' if account.exists() else 'loggedOut', 'credentialRequired': True, 'label': 'Muse test account', 'secret': 'NEVER_EXPOSE'})
    elif method == 'account/loginStart':
        assert p == {'type': 'deviceCode'}
        reply(req, {'verificationUrl': 'https://auth.meta.com/device', 'userCode': 'TEST-CODE', 'secret': 'NEVER_EXPOSE'})
    elif method == 'test/finishLogin':
        account.write_text('signed-in')
        notify('account/loginCompleted', {'outcome': 'granted'})
        reply(req, {})
    elif method == 'account/loginCancel': reply(req, {'cancelled': True})
    elif method == 'account/logout':
        account.unlink(missing_ok=True)
        reply(req, {})
    elif method == 'model/list':
        reply(req, {'models': [
            {'modelId': 'muse-wonder-1.0', 'displayLabel': 'Muse Wonder', 'providerId': 'meta', 'isDefault': True, 'variants': ['minimal', 'high', 'max']},
            {'modelId': 'muse-wonder-full', 'displayLabel': 'Muse Wonder Full', 'providerId': 'meta', 'isDefault': False, 'variants': ['high', 'max']},
            {'modelId': 'other-provider', 'providerId': 'other', 'variants': []}]})
    elif method == 'permissionProfile/list':
        reply(req, {'profiles': [{'profileId': 'read-only', 'label': 'Read-only', 'available': True, 'reviewerAuthority': 'human'}, {'profileId': 'ask-me', 'label': 'Ask me', 'available': True, 'reviewerAuthority': 'human'}]})
    elif method.startswith(('session/', 'turn/', 'approval/', 'userInput/')):
        assert uuid.UUID(p['commandId']).version == 7
        if method == 'session/start':
            assert p['providerId'] == 'meta'
            assert p['permissionProfile'] in {'read-only', 'ask-me'}
            session = p['sessionId']
            reply(req, {'session': {'sessionId': session, 'workspaceRoot': p['workspaceRoot']}, 'viewCursor': ''})
        elif method == 'session/resume':
            session = p['sessionId']
            notify('item/completed', {'sessionId': session, 'item': {'itemId': 'old', 'kind': 'agentMessage', 'status': 'completed', 'text': 'OLD REPLAY'}})
            reply(req, {'session': {'sessionId': session}, 'viewCursor': ''})
        elif method == 'session/setApprovalMode':
            assert p['mode'] == 'onRequest'
            reply(req, {})
        elif method == 'session/setModel': reply(req, {})
        elif method == 'turn/start':
            assert p.get('ifBusy', 'queue') in {'queue', 'steer', 'replace'}
            turn = str(uuid.uuid4())
            reply(req, {'turnId': turn, 'disposition': 'started'})
            text = p['input'][0]['text']
            if text.endswith('wait'): continue
            if text.endswith('fail'):
                done('', 'failed'); continue
            if text.endswith('crash'):
                raise SystemExit(1)
            if text.endswith('question'):
                notify('userInput/requested', {'sessionId': session, 'turnId': turn, 'userInputId': 'question-1', 'questions': [{'id': 'style', 'header': 'Style', 'question': 'Which style?', 'selection': {'mode': 'single'}, 'options': [{'label': 'Simple'}]}]})
                continue
            if text.endswith('approval'):
                permission = {'sessionId': session, 'turnId': turn, 'approvalId': 'approval-1', 'currentRequirementId': {'approvalId': 'approval-1', 'sourceIndex': 1}, 'rawArgs': 'change a file', 'availableChoices': [{'choiceId': 'allow_once', 'decision': 'approved', 'scope': 'once'}, {'choiceId': 'deny', 'decision': 'denied', 'scope': 'once'}]}
                send({'id': 'present-permission', 'method': 'approval/request', 'params': permission})
                continue
            done()
        elif method == 'turn/interrupt':
            reply(req, {})
            done('', 'cancelled')
        elif method == 'approval/decide':
            assert p['requirementId'] == permission['currentRequirementId']
            reply(req, {})
            done('Approved' if p['choiceId'] == 'allow_once' else 'Denied')
        elif method == 'userInput/answer':
            assert p['answers'] == [{'questionId': 'style', 'selectedLabel': 'Simple'}]
            reply(req, {})
            done('Answer received')
    elif method == 'test/unknown':
        send({'id': 'unknown-operation', 'method': 'unknown/grantAccess', 'params': {}})
        reply(req, {})
