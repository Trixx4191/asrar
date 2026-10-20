# Asrār 0.14 — subscription workspace

Asrār now runs Codex tasks through the official local Codex App Server. Sign in
with an eligible ChatGPT account, including Plus, and use that account's Codex
allowance. This does not convert a subscription into a general OpenAI API key.

## Setup

1. Install the Python requirements in the project's `venv`.
2. Run `npm install` in `frontend`; this installs the pinned official Codex CLI.
3. Restart the backend with `venv/bin/python main.py`.
4. Run the desktop frontend with `npm run dev` from `frontend`.
5. Open **Accounts**, choose browser sign-in or device-code sign-in, and finish
   the OpenAI flow yourself. Device authorization must be enabled for your
   account/workspace if required by OpenAI.
6. Open **Settings** to choose a workspace. Read-only is the default. Choose
   workspace access to let Codex edit files and run tools within its sandbox.
7. Start a new chat. Select the account default or an available model and its
   supported reasoning effort in the workspace toolbar.

`ASRAR_CODEX_BIN` can point to an installed Codex executable instead of the
bundled CLI. Asrār never installs or updates the runtime during a chat request.

## Interaction and configuration

- Account controls include sign-in cancellation, status refresh, reconnect,
  sign-out, and account switching. Running tasks must stop before account changes.
- Usage displays the quota windows and reset times returned by Codex. Missing
  usage data is shown as unavailable rather than an invented allowance.
- Preferences are validated and saved atomically in `data/preferences.json`:
  assistant name, default connection/model/effort, workspace, access mode,
  workspace network access, custom Codex instructions, theme, activity visibility,
  and Enter-to-send behavior. The toolbar saves its connection/model choices.
- Chat preserves terminal activity, plans, diffs, replies, and Codex thread IDs.
  Completed text supersedes provisional streamed text. Stopped turns are
  interrupted in Codex as well as in the browser.
- Command/file approvals are queued and tied to a live conversation, thread,
  turn, and one-use request ID. Approval errors retain the request for retry.
  Structured questions support choices and free text, including password inputs
  when the runtime marks a question secret. Unknown server requests fail closed.
- Chats can be searched, copied, and reopened; replies render headings, lists,
  tables, code blocks, and secure links. Starter prompts, keyboard shortcuts,
  readable error messages, retry editing, and responsive themes improve navigation.
- Add up to eight workspace files as context with the desktop picker or relative
  paths in browser mode. Resolved paths, including symlinks, must stay inside
  the selected workspace. Export conversations as Markdown from the chat toolbar.
- **Ctrl/Cmd+Shift+O** starts a new chat. **Ctrl/Cmd+Enter** always sends.
- Switching workspace, runtime, or signed-in account requires a new conversation
  to avoid silently mixing histories. Existing API conversations remain readable.
- Legacy API models and the existing Asrār tool loop remain available in API mode.
  Settings explicitly labeled Codex apply to Codex tasks, not that legacy loop.

## Authentication boundaries

Codex owns OAuth, the callback listener, credential persistence, and refresh.
Asrār starts it with a private `data/codex` home and ChatGPT-only authentication.
It strips inherited API keys/tokens from the child environment, does not read
OAuth credential files, and never routes subscription turns through API adapters.
API keys already saved in `.env` remain available only to explicit API mode.

The private Codex home has mode `0700` on POSIX. The selected Codex file credential
store is local disk storage, not application-level encryption. Protect the local
user account and exclude `data/` from backups shared with others. Sign-out here
affects this private runtime, not the user's VS Code or standalone Codex sign-in.

The API binds to loopback by default. It checks Host and Origin, uses an explicit
CORS allowlist, and requires a non-simple desktop header for account operations
and subscription requests. These are local single-user desktop controls, not
multi-user server authentication. Do not expose this backend publicly.

Read-only/workspace permissions are enforced by Codex's sandbox and its managed
policies. Permission controls do not claim to restrict a trusted command after
the user explicitly approves an escalation. Web search is disabled by default, with cached/live search options in Settings;
workspace networking is configured separately. No unrestricted access preset is
exposed by Asrār.

## Verification

```sh
venv/bin/python -m unittest discover -s tests -v
cd frontend
npm run build:web
```

The protocol tests run a deterministic subprocess peer, exercise JSON-RPC
handshakes, authoritative streaming, plan/diff persistence, restart/resume,
scope checks, one-use approvals, questions, stop/cancellation, failed turns,
account separation, credential isolation, Host/Origin guards, and preferences.
They do not consume a real subscription or establish a successful account login.

A real signed-in task must still be checked after the user completes OpenAI
sign-in. Model access, rate limits, and subscription eligibility are determined
by OpenAI. Other providers' subscription sign-ins are not implemented in this
release; their supported API connections remain available. This release verifies
the local source/development build; a standalone installer with a bundled Python
runtime is a separate distribution task.

The dependency installation audit reported 26 findings in the existing frontend
dependency tree (6 moderate, 18 high, 2 critical). A dependency-security upgrade,
particularly the older Electron/build stack, is still outstanding; this release
does not claim that those audit findings were resolved.

## References

- [Codex App Server protocol](https://learn.chatgpt.com/docs/app-server)
- [Codex authentication](https://learn.chatgpt.com/docs/auth)
- [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
