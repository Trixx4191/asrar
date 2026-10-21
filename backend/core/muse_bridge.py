"""Muse Session Protocol v1; credentials and model entitlements belong to Muse."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import shutil
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from core.kimi_bridge import terminate

ROOT = Path(__file__).resolve().parents[2]
AUTH_HOME = ROOT / "data" / "muse-code"


class MuseError(RuntimeError):
    pass


def command_id():
    # MSP mutations require UUIDv7, including on Python versions before uuid7().
    return str(uuid.UUID(int=(int(time.time() * 1000) << 80) | (7 << 76)
                         | (secrets.randbits(12) << 64) | (2 << 62) | secrets.randbits(62)))


def executable():
    configured = os.environ.get("ASRAR_MUSE_BIN")
    if configured:
        return shutil.which(configured)
    directory = ROOT / "data/muse-runtime/bin"
    # Prefer a verified installed native binary to the self-updating launcher.
    marker = directory / ".muse-version"
    if marker.is_file():
        version = marker.read_text().strip()
        if version and Path(version).name == version:
            native = directory / ("muse-bin-" + version)
            if native.is_file():
                return str(native)
    return shutil.which("muse")


def cli_environment():
    clean = {k: v for k, v in os.environ.items()
             if not k.endswith(("_API_KEY", "_TOKEN")) and not k.startswith(("MUSE_", "META_", "MODEL_"))}
    clean.update({"XDG_CONFIG_HOME": str(AUTH_HOME / "config"), "XDG_DATA_HOME": str(AUTH_HOME / "data"),
                  "XDG_CACHE_HOME": str(AUTH_HOME / "cache"), "MUSE_AUTH_PATH": str(AUTH_HOME / "config/muse/auth.json"),
                  "MUSE_NO_AUTO_UPDATE": "1"})
    return clean


def login_url(value):
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == "https" and parsed.hostname in {"auth.meta.com", "www.meta.com", "meta.com"}
                 and not parsed.username and not parsed.password and parsed.port in (None, 443))
    except ValueError:
        valid = False
    if not valid:
        raise MuseError("Muse returned an unexpected sign-in address")
    return value


class MuseBridge:
    def __init__(self):
        self.process = None
        self._reader = None
        self._start_lock = asyncio.Lock()
        self._auth_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self.pending = {}
        self.listeners = {}
        self.active = {}
        self.approvals = {}
        self.login = None
        self.login_error = None
        self.handshake = {}
        self.network = False

    async def start(self):
        async with self._start_lock:
            if self.process and self.process.returncode is None:
                return
            binary = executable()
            if not binary:
                raise MuseError("Muse Code is not installed. Install the official CLI or set ASRAR_MUSE_BIN, then refresh.")
            from core.preferences import load_preferences
            self.network = load_preferences().network_access
            AUTH_HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
            AUTH_HOME.chmod(0o700)
            self.process = await asyncio.create_subprocess_exec(
                binary, "serve", "--provider", "meta", "--contributor-mode", "off",
                "--sandbox-network", "proxy-only" if self.network else "restricted",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                env=cli_environment(), cwd=str(AUTH_HOME), limit=8 * 1024 * 1024, start_new_session=os.name != "nt")
            self._reader = asyncio.create_task(self._read(self.process))
            try:
                self.handshake = await self._request("initialize", {"clientInfo": {"name": "asrar_desktop", "version": "0.15.0"},
                    "capabilities": {"experimentalApi": True, "userInputDialogs": True}})
                if self.handshake.get("schema", {}).get("version") != 1:
                    raise MuseError("This Muse protocol version is unsupported. Update the integration before continuing.")
                home = self.handshake.get("museHome")
                if not home or not Path(home).resolve().is_relative_to(AUTH_HOME.resolve()):
                    raise MuseError("Muse did not use Asrar's private profile. Reconnect with a runtime that supports XDG directories.")
                await self.send({"method": "initialized"})
            except BaseException:
                await self.close()
                raise

    async def send(self, data):
        async with self._write_lock:
            if not self.process or self.process.returncode is not None:
                raise MuseError("Muse disconnected. Reconnect from Accounts.")
            self.process.stdin.write((json.dumps({"jsonrpc": "2.0", **data}) + "\n").encode())
            await self.process.stdin.drain()

    async def _request(self, method, params=None, timeout=30):
        key = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        try:
            await self.send({"id": key, "method": method, **({"params": params} if params is not None else {})})
            return await asyncio.wait_for(future, timeout)
        except TimeoutError as exc:
            raise MuseError(f"Muse did not answer {method}. Reconnect and try again.") from exc
        finally:
            self.pending.pop(key, None)

    async def request(self, method, params=None, timeout=30):
        await self.start()
        return await self._request(method, params, timeout)

    async def command(self, method, params=None):
        return await self.request(method, {"commandId": command_id(), **(params or {})})

    def deliver(self, queue, event):
        if queue.full():
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait({"method": "asrar/disconnected", "params": {}})
        else:
            queue.put_nowait(event)

    async def _read(self, process):
        try:
            while line := await process.stdout.readline():
                data = json.loads(line)
                if "method" not in data:
                    future = self.pending.get(data.get("id"))
                    if future and not future.done():
                        if "error" in data:
                            code = data["error"].get("code")
                            future.set_exception(MuseError(f"Muse request failed ({code}). Check your sign-in, plan, and runtime version."))
                        else:
                            future.set_result(data.get("result") or {})
                    continue
                await self.dispatch(data)
        except (ValueError, OSError):
            pass
        finally:
            for future in list(self.pending.values()):
                if not future.done():
                    future.set_exception(MuseError("Muse disconnected. Reconnect from Accounts."))
            for queue in list(self.listeners.values()):
                self.deliver(queue, {"method": "asrar/disconnected", "params": {}})
            self.approvals.clear()
            self.login = None

    async def dispatch(self, data):
        method = data["method"]
        params = data.get("params") or {}
        if method == "account/loginCompleted":
            self.login = None
            granted = params.get("outcome") == "granted"
            self.login_error = None if granted else "Muse sign-in did not complete. Try again."
            if granted:
                marker = AUTH_HOME / "asrar-session-id"
                marker.write_text(uuid.uuid4().hex)
                marker.chmod(0o600)
        if "id" in data:
            # The documented approval/request is answered through approval/decide.
            # Decline unknown server operations instead of granting capabilities.
            if method not in {"approval/request", "userInput/request"}:
                await self.send({"id": data["id"], "error": {"code": -32601, "message": "Unsupported client operation"}})
                return
        queue = self.listeners.get(params.get("sessionId"))
        if queue:
            self.deliver(queue, {"method": method, "params": params})
            if "id" in data:
                await self.send({"id": data["id"], "result": {}})
        elif "id" in data:
            await self.send({"id": data["id"], "error": {"code": -32601, "message": "No active session"}})

    async def account(self):
        raw = await self.request("account/read")
        if raw.get("state") == "accountLogin":
            return {"type": "meta-account", "label": raw.get("label")}
        if raw.get("state") not in {None, "loggedOut"}:
            raise MuseError("This connection requires browser account sign-in. Use API mode for Meta API keys.")
        return None

    def scope(self):
        marker = AUTH_HOME / "asrar-session-id"
        if not marker.exists():
            marker.write_text(uuid.uuid4().hex)
            marker.chmod(0o600)
        return marker.read_text()

    async def status(self):
        if not executable():
            return {"installed": False, "connected": False, "account": None,
                    "message": "Install the official Muse Code CLI to enable Meta account sign-in."}
        try:
            account = await self.account()
            return {"installed": True, "connected": bool(account), "account": account,
                    "login": dict(self.login) if self.login else None, "message": self.login_error,
                    "active_turns": len(self.active)}
        except (MuseError, OSError) as exc:
            return {"installed": True, "connected": False, "account": None, "message": str(exc)}

    async def begin_login(self):
        async with self._auth_lock:
            if self.active:
                raise MuseError("Stop running Muse tasks before signing in")
            if self.login:
                return dict(self.login)
            self.login_error = None
            pending = {"loginId": uuid.uuid4().hex}
            self.login = pending
            try:
                result = await self.request("account/loginStart", {"type": "deviceCode"})
                url = login_url(result.get("verificationUrl", ""))
            except BaseException:
                self.login = None
                with contextlib.suppress(MuseError, OSError):
                    await self.request("account/loginCancel")
                raise
            # Completion can arrive in the same stdout batch as the login reply.
            if self.login is pending:
                pending.update(authUrl=url, userCode=result.get("userCode"))
            return dict(self.login) if self.login else {}

    async def cancel_login(self):
        async with self._auth_lock:
            await self.request("account/loginCancel")
            self.login = None

    async def logout(self):
        async with self._auth_lock:
            if self.active:
                raise MuseError("Stop running Muse tasks before signing out")
            if self.login:
                await self.request("account/loginCancel")
            await self.request("account/logout")
            self.login = None
            self.login_error = None
            (AUTH_HOME / "asrar-session-id").unlink(missing_ok=True)

    async def models(self):
        if not await self.account():
            return []
        raw = await self.request("model/list")
        models = []
        for row in raw.get("models", []):
            if row.get("providerId") != "meta":
                continue
            variants = row.get("variants")
            models.append({"id": row["modelId"], "displayName": row.get("displayLabel") or row["modelId"],
                           "isDefault": bool(row.get("isDefault")),
                           "supportedReasoningEfforts": [{"reasoningEffort": v} for v in variants] if isinstance(variants, list) else []})
        return models

    async def decide(self, conversation_id, action_id, approved):
        ticket = self.approvals.get(action_id)
        if not ticket or ticket["conversation_id"] != conversation_id or ticket.get("kind") != "approval":
            raise MuseError("This approval is no longer pending for this conversation")
        choices = ticket["params"].get("availableChoices", [])
        choice = next((c for c in choices if c.get("scope") == "once" and c.get("decision") == ("approved" if approved else "denied")), None)
        if not choice:
            raise MuseError("Muse does not offer this one-time decision. Stop the task to decline it.")
        # Claim once before the await; a failed RPC must not submit a duplicate mutation.
        self.approvals.pop(action_id)
        p = ticket["params"]
        await self.command("approval/decide", {"sessionId": p["sessionId"], "approvalId": p["approvalId"],
                          "requirementId": p["currentRequirementId"], "choiceId": choice["choiceId"]})

    async def answer(self, conversation_id, action_id, answers):
        ticket = self.approvals.get(action_id)
        if not ticket or ticket["conversation_id"] != conversation_id or ticket.get("kind") != "question":
            raise MuseError("This question is no longer pending for this conversation")
        p = ticket["params"]
        if set(answers) != {q["id"] for q in p["questions"]} or any(not a.strip() or len(a) > 500 for a in answers.values()):
            raise MuseError("Answer every question (up to 500 characters each)")
        values = []
        for q in p["questions"]:
            labels = {o["label"] for o in q.get("options", [])}
            if q.get("selection", {}).get("mode") == "multiple":
                raise MuseError("This question requires multiple selections. Stop and answer it in Muse Code.")
            a = answers[q["id"]]
            values.append({"questionId": q["id"], "selectedLabel" if a in labels else "freeText": a})
        self.approvals.pop(action_id)
        await self.command("userInput/answer", {"sessionId": p["sessionId"], "userInputId": p["userInputId"], "answers": values})

    async def interrupt(self, conversation_id):
        active = self.active.get(conversation_id)
        if active:
            active["stopped"] = True
        if active and active.get("session_id") and self.process and self.process.returncode is None:
            await self.command("turn/interrupt", {"sessionId": active["session_id"], **({"turnId": active["turn_id"]} if active.get("turn_id") else {})})

    async def close(self):
        process, self.process = self.process, None
        await terminate(process)
        if self._reader and self._reader is not asyncio.current_task():
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader


bridge = MuseBridge()
