"""Kimi Code account login and ACP transport. API providers remain independent."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import signal
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
AUTH_HOME = ROOT / "data" / "kimi-code"


class KimiError(RuntimeError):
    pass


def executable():
    configured = os.environ.get("ASRAR_KIMI_BIN")
    if configured:
        return shutil.which(configured)
    bundled = ROOT / "data" / "kimi-runtime" / ("Scripts/kimi.exe" if os.name == "nt" else "bin/kimi")
    if bundled.is_file():
        return str(bundled)
    standalone = ROOT / "data" / "kimi-bundle"
    for pattern in ("kimi", "*/kimi", "*/*/kimi"):
        for candidate in standalone.glob(pattern):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return shutil.which("kimi")


def prepare_home():
    AUTH_HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
    AUTH_HOME.chmod(0o700)


def cli_environment():
    clean = {k: v for k, v in os.environ.items()
             if not k.endswith(("_API_KEY", "_TOKEN"))
             and not k.startswith(("KIMI_", "MOONSHOT_", "OPENAI_"))}
    clean.update(KIMI_SHARE_DIR=str(AUTH_HOME), NO_COLOR="1", PYTHONUNBUFFERED="1",
                 BROWSER="true")
    return clean


def trusted_login_url(value):
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == "https" and parsed.hostname in {
            "auth.kimi.com", "auth.kimi.ai", "kimi.com", "kimi.ai", "www.kimi.com", "www.kimi.ai"}
            and not parsed.username and not parsed.password and parsed.port in (None, 443))
    except ValueError:
        valid = False
    if not valid:
        raise KimiError("Kimi returned an unexpected sign-in address")
    return value


async def spawn(*args, cwd=None, login=False):
    binary = executable()
    if not binary:
        raise KimiError("Install Kimi Code CLI (kimi-cli) and refresh to enable account sign-in")
    prepare_home()
    return await asyncio.create_subprocess_exec(
        binary, *args, cwd=cwd or str(AUTH_HOME), env=cli_environment(),
        stdin=asyncio.subprocess.DEVNULL if login else asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT if login else asyncio.subprocess.DEVNULL,
        limit=1024 * 1024, start_new_session=os.name != "nt")


async def terminate(process):
    if not process or process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    try:
        await asyncio.wait_for(process.wait(), 3)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        await process.wait()


class ACPClient:
    def __init__(self, process, on_message):
        self.process = process
        self.on_message = on_message
        self.pending = {}
        self.serial = 0
        self.reader = asyncio.create_task(self._read())

    async def send(self, data):
        self.process.stdin.write((json.dumps({"jsonrpc": "2.0", **data}) + "\n").encode())
        await self.process.stdin.drain()

    async def request(self, method, params, timeout=30):
        self.serial += 1
        key = self.serial
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        try:
            await self.send({"id": key, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout) if timeout else await future
        finally:
            self.pending.pop(key, None)

    async def _read(self):
        try:
            while line := await self.process.stdout.readline():
                data = json.loads(line)
                if "method" in data:
                    await self.on_message(self, data)
                elif (future := self.pending.get(data.get("id"))) is not None and not future.done():
                    if "error" in data:
                        # Do not relay arbitrary runtime payloads, which may contain credentials.
                        code = data["error"].get("code")
                        future.set_exception(KimiError("Sign in with Kimi in Accounts first" if code == -32000
                                                       else f"Kimi request failed ({code}). Reconnect and try again."))
                    else:
                        future.set_result(data.get("result") or {})
        except (ValueError, OSError):
            pass
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(KimiError("Kimi stopped responding. Reconnect and try again."))

    async def close(self):
        await terminate(self.process)
        self.reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.reader


class KimiBridge:
    def __init__(self):
        self.active = {}
        self.approvals = {}
        self.login = None
        self.login_error = None
        self._login_process = None
        self._login_task = None
        self._auth_lock = asyncio.Lock()
        self._catalog = []

    def account(self):
        try:
            data = json.loads((AUTH_HOME / "credentials/kimi-code.json").read_text())
            if data.get("access_token") and (data.get("refresh_token") or data.get("expires_at", 0) > time.time()):
                return {"type": "kimi-code"}
        except (OSError, ValueError, TypeError):
            pass
        return None

    def scope(self):
        marker = AUTH_HOME / "asrar-session-id"
        if not marker.exists():
            marker.write_text(uuid.uuid4().hex)
            marker.chmod(0o600)
        return marker.read_text()

    async def status(self):
        return {"installed": bool(executable()), "connected": bool(self.account()),
                "account": self.account(), "login": dict(self.login) if self.login else None,
                "message": self.login_error, "active_turns": len(self.active)}

    async def begin_login(self):
        async with self._auth_lock:
            if self.active:
                raise KimiError("Stop running Kimi tasks before signing in")
            if self.login:
                return dict(self.login)
            self.login_error = None
            self._login_process = await spawn("login", login=True)
            self.login = {"loginId": uuid.uuid4().hex}
            self._login_task = asyncio.create_task(self._watch_login(self._login_process))
            return dict(self.login)

    async def _watch_login(self, process):
        try:
            async with asyncio.timeout(600):
                while line := await process.stdout.readline():
                    # Forward only an official verification link, never stdout or tokens.
                    decoded = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line.decode(errors="replace"))
                    match = re.search(r"https://[^\s<>\"']+", decoded)
                    if match:
                        self.login["authUrl"] = trusted_login_url(match[0].rstrip(".,)"))
                code = await process.wait()
                if code or not self.account():
                    raise KimiError("Kimi sign-in did not complete. Check your account eligibility and try again.")
                (AUTH_HOME / "asrar-session-id").unlink(missing_ok=True)
                self.scope()
                self._catalog = []
        except asyncio.CancelledError:
            raise
        except (KimiError, OSError, TimeoutError) as exc:
            self.login_error = str(exc) or "Kimi sign-in timed out. Try again."
        finally:
            await terminate(process)
            self.login = None
            self._login_process = None

    async def cancel_login(self):
        async with self._auth_lock:
            if self._login_task:
                self._login_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._login_task
                self._login_task = None
            await terminate(self._login_process)
            self._login_process = None
            self.login = None

    async def logout(self):
        if self.active:
            raise KimiError("Stop running Kimi tasks before signing out")
        await self.cancel_login()
        async with self._auth_lock:
            # This private directory is owned solely by Asrar, never the user's CLI.
            if AUTH_HOME.exists():
                shutil.rmtree(AUTH_HOME)
            self._catalog = []
            self.login_error = None

    async def decide(self, conversation_id, action_id, approved):
        ticket = self.approvals.get(action_id)
        if not ticket or ticket["conversation_id"] != conversation_id:
            raise KimiError("This approval is no longer pending for this conversation")
        self.approvals.pop(action_id)
        option = next((o for o in ticket["options"] if o["kind"] == ("allow_once" if approved else "reject_once")), None)
        outcome = {"outcome": "selected", "optionId": option["optionId"]} if option else {"outcome": "cancelled"}
        await ticket["client"].send({"id": ticket["id"], "result": {"outcome": outcome}})

    async def interrupt(self, conversation_id):
        active = self.active.get(conversation_id)
        if not active:
            return
        active["stopped"] = True
        client = active.get("client")
        if client:
            if active.get("session_id"):
                with contextlib.suppress(Exception):
                    await client.send({"method": "session/cancel", "params": {"sessionId": active["session_id"]}})
            await client.close()

    async def close(self):
        await self.cancel_login()
        for conversation_id in list(self.active):
            await self.interrupt(conversation_id)


bridge = KimiBridge()
