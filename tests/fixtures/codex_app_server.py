#!/usr/bin/env python3
"""Deterministic Codex protocol peer for transport tests. No network/tools."""
import json
import sys

waiting = None
signed_in = True

def send(value):
    print(json.dumps(value), flush=True)

def event(method, params):
    send({"method": method, "params": {"threadId": "thread-test", "turnId": "turn-test", **params}})

def finish():
    event("item/agentMessage/delta", {"itemId": "reply", "delta": "Hello from Codex"})
    event("item/completed", {"item": {"id": "reply", "type": "agentMessage", "text": "Hello from Codex."}})
    event("turn/completed", {"turn": {"id": "turn-test", "status": "completed"}})

for line in sys.stdin:
    msg = json.loads(line)
    method, identifier, params = msg.get("method"), msg.get("id"), msg.get("params", {})
    if method == "initialized":
        continue
    if method is None:
        if identifier in {"server-approval", "server-question"} and waiting:
            event("serverRequest/resolved", {"requestId": identifier})
            waiting = None
            finish()
        continue
    result = {}
    if method == "account/read":
        result = {"account": {"type": "chatgpt", "email": "test@example.invalid", "planType": "plus", "accessToken": "NEVER_EXPOSE"} if signed_in else None}
    elif method == "account/logout":
        signed_in = False
    elif method == "account/login/start":
        result = {"type": "chatgpt", "loginId": "login-test", "authUrl": "https://auth.openai.com/authorize?state=example"}
    elif method == "model/list":
        result = {"data": [{"id": "test-codex", "model": "test-codex", "displayName": "Test Codex", "isDefault": True, "supportedReasoningEfforts": [{"reasoningEffort": "medium"}]}], "nextCursor": None}
    elif method in {"thread/start", "thread/resume"}:
        if params.get("approvalPolicy") != "on-request":
            send({"id": identifier, "error": {"message": "Expected guarded approval policy"}})
            continue
        result = {"thread": {"id": "thread-test"}}
    elif method == "turn/start":
        if params.get("approvalPolicy") != "on-request":
            send({"id": identifier, "error": {"message": "Expected sandbox escalation approval policy"}})
            continue
        result = {"turn": {"id": "turn-test", "status": "inProgress"}}
    elif method == "account/rateLimits/read":
        result = {"rateLimits": {"primary": {"usedPercent": 23, "resetsAt": 1900000000}}}
    send({"id": identifier, "result": result})
    if method == "turn/start":
        text = params["input"][0]["text"]
        if "question" in text:
            waiting = True
            send({"id": "server-question", "method": "item/tool/requestUserInput", "params": {"threadId": "thread-test", "turnId": "turn-test", "itemId": "question-test", "questions": [{"id": "preference", "header": "Style", "question": "Which style?", "options": [{"label": "Simple", "description": "Keep it simple"}]}]}})
        elif "approval" in text:
            waiting = True
            send({"id": "server-approval", "method": "item/commandExecution/requestApproval", "params": {"threadId": "thread-test", "turnId": "turn-test", "itemId": "command-test", "command": "echo hello", "cwd": params["cwd"]}})
        elif "wait" in text:
            waiting = True
        elif "fail" in text:
            event("turn/completed", {"turn": {"id": "turn-test", "status": "failed", "error": {"message": "Usage limit reached"}}})
        else:
            event("turn/plan/updated", {"plan": [{"step": "Inspect project", "status": "completed"}]})
            event("turn/diff/updated", {"diff": "+example change"})
            finish()
    if method == "turn/interrupt":
        waiting = None
        event("turn/completed", {"turn": {"id": "turn-test", "status": "interrupted"}})
