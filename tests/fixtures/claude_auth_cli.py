#!/usr/bin/env python3
"""Fake official auth commands; dummy codes only, no network or real OAuth."""
import json
import os
import sys
from pathlib import Path

root=Path(os.environ['CLAUDE_CONFIG_DIR'])
state=root/'fake-account.json'
args=sys.argv
if 'status' in args:
    account=json.loads(state.read_text()) if state.exists() else {'loggedIn':False,'authMethod':'none'}
    print(json.dumps(account),flush=True)
    sys.exit(0 if account['loggedIn'] else 1)
if 'login' in args:
    print('Open https://claude.ai/oauth/authorize?state=fake-test',flush=True)
    print('Paste verification code:',flush=True)
    code=sys.stdin.readline().strip()
    if code=='verification-test':
        state.write_text(json.dumps({'loggedIn':True,'authMethod':'claude.ai','email':'test@example.invalid','subscriptionType':'pro','accessToken':'NEVER_EXPOSE'}))
        sys.exit(0)
    sys.exit(1)
if 'logout' in args:
    state.write_text(json.dumps({'loggedIn':False,'authMethod':'none'}))
    sys.exit(0)
sys.exit(2)
