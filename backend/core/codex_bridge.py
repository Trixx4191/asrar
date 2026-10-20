"""Official Codex App Server transport. OAuth secrets remain owned by Codex.

Asrār uses a private CODEX_HOME, identifies itself honestly, and never forwards
ChatGPT tokens to an API adapter. Subscription turns never fall back to an API.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from providers.configuration import redact_secrets

ROOT = Path(__file__).resolve().parents[2]
AUTH_HOME = ROOT / "data" / "codex"


class CodexError(RuntimeError):
    pass


def executable() -> str | None:
    configured = os.environ.get("ASRAR_CODEX_BIN")
    if configured:
        return shutil.which(configured)
    local = ROOT / "frontend" / "node_modules" / ".bin" / ("codex.cmd" if os.name == "nt" else "codex")
    return str(local) if local.is_file() else shutil.which("codex")


def login_url(value: str) -> str:
    url = urlsplit(value)
    if url.scheme != "https" or url.hostname not in {"auth.openai.com", "chatgpt.com"} or url.username or url.password:
        raise CodexError("Codex returned an unexpected sign-in address")
    return value


class CodexBridge:
    def __init__(self):
        self.process = None
        self._start_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._auth_lock = asyncio.Lock()
        self._reader_task = None
        self._stderr_task = None
        self._sequence = 0
        self._pending = {}
        self.listeners = {}
        self.approvals = {}
        self.login = None
        self.login_error = None
        self.active = {}  # conversation id -> thread/turn, owned by one stream
        self.last_error = None

    async def start(self):
        async with self._start_lock:
            if self.process and self.process.returncode is None:
                return
            binary = executable()
            if not binary:
                raise CodexError("Codex is not installed. Run npm install in the frontend folder, then reconnect.")
            AUTH_HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
            AUTH_HOME.chmod(0o700)
            # Do not inherit .env credentials or another client's Codex session.
            env = {key: value for key, value in os.environ.items()
                   if not key.endswith(("_API_KEY", "_TOKEN")) and key not in {"OPENAI_BASE_URL", "CODEX_HOME"}}
            env["CODEX_HOME"] = str(AUTH_HOME)
            self.process = await asyncio.create_subprocess_exec(
                binary, "app-server", "-c", 'forced_login_method="chatgpt"',
                "-c", 'cli_auth_credentials_store="file"',
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, env=env, cwd=str(ROOT), limit=8 * 1024 * 1024,
            )
            self._reader_task = asyncio.create_task(self._read(self.process))
            self._stderr_task = asyncio.create_task(self._drain_stderr(self.process))
            try:
                await self._request("initialize", {"clientInfo": {"name": "asrar_desktop", "title": "Asrār", "version": "0.14.0"}})
                await self._send({"method": "initialized", "params": {}})
                self.last_error = None
            except BaseException:
                await self.close()
                raise

    async def _drain_stderr(self, process):
        # Drain to prevent a blocked child. Do not persist auth URLs or tokens.
        while await process.stderr.read(8192):
            pass

    async def _send(self, payload):
        async with self._write_lock:
            if not self.process or self.process.returncode is not None:
                raise CodexError("Codex disconnected. Reconnect from Accounts.")
            self.process.stdin.write((json.dumps(payload) + "\n").encode())
            await self.process.stdin.drain()

    async def _request(self, method, params=None, timeout=25):
        self._sequence += 1
        identifier = self._sequence
        future = asyncio.get_running_loop().create_future()
        self._pending[identifier] = future
        try:
            await self._send({"id": identifier, "method": method, "params": params or {}})
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as exc:
            raise CodexError(f"Codex did not answer {method}. Reconnect and try again.") from exc
        finally:
            self._pending.pop(identifier, None)

    async def request(self, method, params=None, timeout=25):
        await self.start()
        return await self._request(method, params, timeout)

    async def _read(self, process):
        try:
            while line := await process.stdout.readline():
                try:
                    message = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                await self._dispatch(message)
        except (ValueError, OSError, asyncio.IncompleteReadError):
            pass
        finally:
            error = CodexError("Codex disconnected. Reconnect from Accounts.")
            self.last_error = str(error)
            for future in list(self._pending.values()):
                if not future.done():
                    future.set_exception(error)
            for queue in list(self.listeners.values()):
                self.deliver(queue, {"method": "asrar/disconnected", "params": {}})
            self.approvals.clear()
            self.login = None

    async def _dispatch(self, message):
        method = message.get("method")
        if not method:
            future = self._pending.get(message.get("id"))
            if future and not future.done():
                if "error" in message:
                    future.set_exception(CodexError(redact_secrets(str(message["error"].get("message", "Codex request failed")))))
                else:
                    future.set_result(message.get("result", {}))
            return
        params = message.get("params") or {}
        if method == "account/login/completed":
            self.login = None
            self.login_error = None if params.get("success") else "Sign-in did not complete. Try connecting again."
            if params.get("success"):
                marker = AUTH_HOME / "asrar-session-id"
                marker.write_text(uuid.uuid4().hex)
                marker.chmod(0o600)
        thread_id = params.get("threadId")
        queue = self.listeners.get(thread_id)
        if "id" in message:
            # Never accept a server request automatically. Only these two
            # documented approval types can be accepted via the human UI.
            if queue and method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval", "item/tool/requestUserInput"}:
                ticket = uuid.uuid4().hex
                self.approvals[ticket] = {"rpc_id": message["id"], "thread_id": thread_id,
                                          "turn_id": params.get("turnId"), "method": method,
                                          "questions": params.get("questions", []), "answered": False}
                params = {**params, "approvalId": ticket}
            else:
                await self._send({"id": message["id"], "error": {"code": -32601, "message": "This client does not support this request"}})
                if queue:
                    self.deliver(queue, {"method": "asrar/notice", "params": {"message": "Codex requested an unsupported interaction. It was declined."}})
                return
        if method == "serverRequest/resolved":
            for ticket, approval in list(self.approvals.items()):
                if approval["rpc_id"] == params.get("requestId"):
                    params = {**params, "approvalId": ticket}
                    self.approvals.pop(ticket, None)
        if method == "turn/started":
            for active in self.active.values():
                if active.get("thread_id") == thread_id:
                    active["turn_id"] = params.get("turn", {}).get("id")
        if queue:
            self.deliver(queue, {"method": method, "params": params})

    @staticmethod
    def deliver(queue, event):
        if queue.full():
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait({"method": "asrar/disconnected", "params": {}})
        else:
            queue.put_nowait(event)

    async def account(self):
        result = await self.request("account/read", {"refreshToken": False})
        raw = result.get("account")
        if not raw or raw.get("type") != "chatgpt":
            return None
        # Explicit allowlist: never expose token material from protocol additions.
        return {"type": "chatgpt", "email": raw.get("email"), "plan": raw.get("planType")}

    async def status(self):
        if not executable():
            return {"installed": False, "connected": False, "account": None, "message": "Install the Codex runtime with npm install in frontend."}
        try:
            account = await self.account()
            return {"installed": True, "connected": bool(account), "account": account,
                    "login": self.login, "message": self.login_error, "active_turns": len(self.active)}
        except (CodexError, OSError) as exc:
            return {"installed": True, "connected": False, "account": None, "message": redact_secrets(str(exc))}

    async def begin_login(self, device=False):
        async with self._auth_lock:
            return await self._begin_login(device)

    async def _begin_login(self, device=False):
        if self.active:
            raise CodexError("Stop running tasks before switching accounts")
        if self.login:
            return self.login
        result = await self.request("account/login/start", {"type": "chatgptDeviceCode" if device else "chatgpt"})
        self.login_error = None
        self.login = {key: result[key] for key in ("type", "loginId", "userCode") if key in result}
        url_key = "verificationUrl" if device else "authUrl"
        self.login[url_key] = login_url(result[url_key])
        return self.login

    async def cancel_login(self):
        if self.login:
            await self.request("account/login/cancel", {"loginId": self.login["loginId"]})
            self.login = None

    async def logout(self):
        if self.active:
            raise CodexError("Stop running tasks before signing out")
        await self.cancel_login()
        await self.request("account/logout")
        self.login_error = None

    async def models(self):
        models, cursor = [], None
        for _ in range(20):
            result = await self.request("model/list", {"limit": 100, "cursor": cursor, "includeHidden": False})
            models.extend(row for row in result.get("data", []) if not row.get("hidden"))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return models

    async def decide(self, conversation_id, ticket, approved):
        approval = self._scoped_request(conversation_id, ticket)
        if approval["method"] not in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
            raise CodexError("This request needs an answer, not an approval")
        approval["answered"] = True
        await self._send({"id": approval["rpc_id"], "result": {"decision": "accept" if approved else "decline"}})

    def _scoped_request(self, conversation_id, ticket):
        approval = self.approvals.get(ticket)
        active = self.active.get(conversation_id)
        if (not approval or approval.get("answered") or not active or approval["thread_id"] != active.get("thread_id")
                or approval["turn_id"] != active.get("turn_id")):
            raise CodexError("This approval has expired or belongs to another conversation")
        return approval

    async def answer(self, conversation_id, ticket, answers):
        request = self._scoped_request(conversation_id, ticket)
        if request["method"] != "item/tool/requestUserInput":
            raise CodexError("This request needs an approval decision")
        expected = {q["id"] for q in request["questions"]}
        if set(answers) != expected or any(not isinstance(answer, str) or not answer.strip() or len(answer) > 4000 for answer in answers.values()):
            raise CodexError("Answer every question before continuing")
        request["answered"] = True
        await self._send({"id": request["rpc_id"], "result": {"answers": {key: {"answers": [value]} for key, value in answers.items()}}})

    async def interrupt(self, conversation_id):
        active = self.active.get(conversation_id)
        if active and active.get("turn_id"):
            await self.request("turn/interrupt", {"threadId": active["thread_id"], "turnId": active["turn_id"]})

    async def close(self):
        process, self.process = self.process, None
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 4)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        for task in (self._reader_task, self._stderr_task):
            if task and task is not asyncio.current_task():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task


bridge = CodexBridge()
