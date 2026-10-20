"""Claude Code subscription login and SDK lifecycle, with runtime-owned OAuth."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from providers.configuration import redact_secrets

ROOT = Path(__file__).resolve().parents[2]
AUTH_HOME = ROOT / "data" / "claude-code"


class ClaudeError(RuntimeError):
    pass


def executable():
    configured = os.environ.get("ASRAR_CLAUDE_BIN")
    if configured:
        return shutil.which(configured)
    spec = importlib.util.find_spec("claude_agent_sdk")
    if spec and spec.submodule_search_locations:
        binary = Path(next(iter(spec.submodule_search_locations))) / "_bundled" / ("claude.exe" if os.name == "nt" else "claude")
        if binary.is_file():
            return str(binary)
    return shutil.which("claude")


def prepare_home():
    AUTH_HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
    AUTH_HOME.chmod(0o700)


def cli_environment():
    """A subscription runtime must not inherit the server's .env API credentials."""
    result = {name: value for name, value in os.environ.items()
              if not name.endswith(("_API_KEY", "_TOKEN"))
              and not name.startswith(("ANTHROPIC_", "CLAUDE_", "CLAUDECODE"))}
    result.update({"CLAUDE_CONFIG_DIR": str(AUTH_HOME), "CLAUDE_AGENT_SDK_CLIENT_APP": "asrar-desktop/0.14",
                   "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
    return result


def sdk_environment():
    # SDK env is merged with the parent. Empty overrides neutralize inherited
    # API keys/provider selectors without mutating the process environment.
    clean = cli_environment()
    return {**{name: "" for name in os.environ if name not in clean}, **clean}


def trusted_login_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.hostname not in {
            "claude.ai", "claude.com", "platform.claude.com", "console.anthropic.com"}
            or parsed.username or parsed.password):
        raise ClaudeError("Claude Code returned an unexpected sign-in address")
    return value


def sdk_types():
    try:
        import claude_agent_sdk
        return claude_agent_sdk
    except ImportError as exc:
        raise ClaudeError("Install the Python requirements to enable Claude Code") from exc


class ClaudeBridge:
    def __init__(self):
        self.active = {}
        self.approvals = {}
        self.login = None
        self.login_error = None
        self._login_process = None
        self._login_task = None
        self._auth_lock = asyncio.Lock()
        self._catalog_lock = asyncio.Lock()
        self._catalog = None
        self._catalog_time = 0
        self._catalog_scope = None
        self.rate_limit = None

    async def command(self, *args, timeout=20):
        binary = executable()
        if not binary:
            raise ClaudeError("Claude Code is not installed. Install the Python requirements first.")
        prepare_home()
        process = await asyncio.create_subprocess_exec(
            binary, *args, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=cli_environment(), cwd=str(AUTH_HOME), limit=1024 * 1024,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout)
            return process.returncode, stdout.decode(errors="replace")
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def account(self):
        code, output = await self.command("auth", "status")
        try:
            raw = json.loads(output)
        except ValueError as exc:
            raise ClaudeError("Claude Code could not report account status. Reconnect and try again.") from exc
        if code or not raw.get("loggedIn"):
            return None
        if raw.get("authMethod") != "claude.ai":
            raise ClaudeError("This connection requires Claude subscription sign-in. API/Console credentials are not used here.")
        return {"type": "claude.ai", "email": raw.get("email"), "plan": raw.get("subscriptionType")}

    async def require_account(self):
        async with self._auth_lock:
            if self.login:
                raise ClaudeError("Finish or cancel the pending Claude sign-in before starting a task")
            account = await self.account()
            if not account:
                raise ClaudeError("Connect your Claude account in Accounts before sending a task")
            return account

    async def status(self):
        if not executable() or not importlib.util.find_spec("claude_agent_sdk"):
            return {"installed": False, "connected": False, "account": None,
                    "message": "Install the Python requirements to enable Claude Code."}
        try:
            account = await self.account()
            return {"installed": True, "connected": bool(account), "account": account,
                    "login": self.login, "message": self.login_error, "active_turns": len(self.active)}
        except (ClaudeError, OSError, asyncio.TimeoutError) as exc:
            return {"installed": True, "connected": False, "account": None,
                    "message": redact_secrets(str(exc)) or "Claude Code did not respond. Try reconnecting."}

    async def begin_login(self):
        async with self._auth_lock:
            if self.active:
                raise ClaudeError("Stop running Claude tasks before switching accounts")
            if self.login:
                return self.login
            binary = executable()
            if not binary:
                raise ClaudeError("Install the Python requirements first")
            prepare_home()
            # The official CLI owns OAuth and its callback listener. No tokens
            # are exchanged or saved by the application.
            self._login_process = await asyncio.create_subprocess_exec(
                binary, "--settings", '{"forceLoginMethod":"claudeai"}',
                "auth", "login", stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                env=cli_environment(), cwd=str(AUTH_HOME), limit=1024 * 1024,
            )
            self.login = {"loginId": uuid.uuid4().hex, "authUrl": None, "requiresCode": False}
            self.login_error = None
            self._login_task = asyncio.create_task(self._watch_login(self._login_process))
            return dict(self.login)

    async def _watch_login(self, process):
        try:
            async with asyncio.timeout(300):
                buffer = ""
                while chunk := await process.stdout.read(2048):
                    buffer = (buffer + chunk.decode(errors="replace"))[-16000:]
                    if self.login:
                        for match in re.finditer(r"https://[^\s\x1b\x07<>]+", buffer):
                            try:
                                self.login["authUrl"] = trusted_login_url(match.group().rstrip(').,\"'))
                            except ClaudeError:
                                continue
                        if "paste" in buffer.lower() and "code" in buffer.lower():
                            self.login["requiresCode"] = True
                await process.wait()
                if process.returncode == 0 and await self.account():
                    marker = AUTH_HOME / "asrar-session-id"
                    marker.write_text(uuid.uuid4().hex)
                    marker.chmod(0o600)
                    self._catalog = None
                    self.login_error = None
                else:
                    self.login_error = "Claude sign-in did not complete. Try signing in again."
        except asyncio.TimeoutError:
            self.login_error = "Claude sign-in expired. Start a new sign-in."
        except asyncio.CancelledError:
            raise
        except Exception:
            self.login_error = "Claude sign-in could not complete. Reconnect and try again."
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            self.login = None
            self._login_process = None

    async def submit_code(self, code):
        async with self._auth_lock:
            if not self.login or not self._login_process or self._login_process.returncode is not None:
                raise ClaudeError("This sign-in has expired")
            if not code.strip() or len(code) > 4096 or "\n" in code or "\r" in code:
                raise ClaudeError("Paste the single verification code returned by Claude")
            self._login_process.stdin.write((code.strip() + "\n").encode())
            await self._login_process.stdin.drain()

    async def _cancel_login(self):
        if self._login_task and not self._login_task.done():
            self._login_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._login_task
        self.login = None

    async def cancel_login(self):
        async with self._auth_lock:
            await self._cancel_login()

    async def logout(self):
        async with self._auth_lock:
            if self.active:
                raise ClaudeError("Stop running Claude tasks before signing out")
            await self._cancel_login()
            code, _ = await self.command("auth", "logout")
            if code:
                raise ClaudeError("Claude Code could not sign out. Reconnect and try again.")
            self._catalog = None
            self.rate_limit = None
            self.login_error = None

    def scope(self, account):
        prepare_home()
        marker = AUTH_HOME / "asrar-session-id"
        if not marker.exists():
            marker.write_text(uuid.uuid4().hex)
            marker.chmod(0o600)
        return hashlib.sha256(((account.get("email") or "") + marker.read_text()).encode()).hexdigest()

    async def models(self):
        account = await self.require_account()
        scope = self.scope(account)
        async with self._catalog_lock:
            if self._catalog is not None and scope == self._catalog_scope and time.monotonic() - self._catalog_time < 60:
                return self._catalog
            sdk = sdk_types()
            options = sdk.ClaudeAgentOptions(cli_path=executable(), cwd=str(AUTH_HOME), env=sdk_environment(),
                                            setting_sources=[], tools=[], strict_mcp_config=True,
                                            settings='{"forceLoginMethod":"claudeai"}',
                                            stderr=lambda _: None)
            try:
                async with asyncio.timeout(25):
                    async with sdk.ClaudeSDKClient(options=options) as client:
                        info = await client.get_server_info() or {}
            except Exception as exc:
                raise ClaudeError("Claude Code could not load its models. Reconnect and try again.") from exc
            models = []
            for model in info.get("models", []):
                identifier = model.get("value") or model.get("model") or model.get("id")
                if not identifier:
                    continue
                levels = model.get("supportedEffortLevels") or []
                models.append({"id": identifier, "model": identifier,
                               "displayName": model.get("displayName") or identifier,
                               "supportedReasoningEfforts": [{"reasoningEffort": level} for level in levels]})
            self._catalog, self._catalog_scope, self._catalog_time = models, scope, time.monotonic()
            return models

    def scoped_request(self, conversation_id, ticket, kind):
        request = self.approvals.get(ticket)
        active = self.active.get(conversation_id)
        if (not request or request["conversation_id"] != conversation_id or not active
                or request["turn_id"] != active["turn_id"] or request["future"].done() or request["kind"] != kind):
            raise ClaudeError("This request expired or belongs to another conversation")
        return request

    async def decide(self, conversation_id, ticket, approved):
        request = self.scoped_request(conversation_id, ticket, "approval")
        request["future"].set_result(bool(approved))

    async def answer(self, conversation_id, ticket, answers):
        request = self.scoped_request(conversation_id, ticket, "question")
        if (set(answers) != set(request["question_ids"]) or
                any(not isinstance(value, str) or not value.strip() or len(value) > 4000 for value in answers.values())):
            raise ClaudeError("Answer every question before continuing")
        request["future"].set_result(answers)

    async def interrupt(self, conversation_id):
        active = self.active.get(conversation_id)
        if active:
            active["stopped"] = True
            for request in self.approvals.values():
                if request["conversation_id"] == conversation_id and not request["future"].done():
                    request["future"].cancel()
            if active.get("client"):
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(active["client"].interrupt(), 5)
            if active.get("task") and not active["task"].done():
                active["task"].cancel()

    async def close(self):
        await self.cancel_login()
        for conversation_id in list(self.active):
            await self.interrupt(conversation_id)
        tasks = [item["task"] for item in self.active.values() if item.get("task")]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


bridge = ClaudeBridge()
