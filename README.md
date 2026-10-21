# Asrār

> *أسرار* — Arabic for "Secrets / Mysteries"

A local agentic AI assistant. It routes each task to a configured model,
plans multi-step work, edits files with reviewable diffs, runs
its own tests before calling a task done, and asks for real approval before
touching anything destructive.

Full behavior spec and version history: [`ASRAR.md`](./ASRAR.md).
Codebase map: [`LAYOUT.md`](./LAYOUT.md).
Model setup, chat registration, and key troubleshooting: [`MODEL_CONNECTIONS.md`](./MODEL_CONNECTIONS.md).

---

## What it does

| Capability | |
|---|---|
| ChatGPT subscription sign-in | Connect through the official Codex App Server with browser or device-code sign-in; no API key required for Codex tasks |
| Claude subscription sign-in | Connect Claude Code with an eligible Claude plan through the official Agent SDK; separate from Anthropic API billing |
| Workspace controls | Choose a folder, read-only or workspace-write access, network permissions, and custom instructions |
| Account and model controls | Discover available Codex models and reasoning efforts, view subscription usage, reconnect, or switch accounts |
| Multi-model routing | Routes locally by default, honors manual selection, and falls back after provider failures |
| Agentic tool loop | Reads/writes files, greps the codebase, runs shell commands, browses the web, up to 10 iterations per turn |
| Self-verification | Runs the project's tests (or a quick script check) after code edits, before saying a task is done |
| Codebase awareness | Searches file contents and filenames before editing, instead of guessing at structure |
| Diff review | Shows the exact unified diff for every file edit, not just "done" |
| Human-gated approval | Destructive shell commands need a real Approve/Deny click — the model can't self-certify consent |
| Circuit breaker + retry | Backs off a provider after repeated failures; retries transient errors before falling back to a worse model |
| Concurrent tools | Runs independent reads (search/grep/list) in parallel instead of one at a time |
| Persistent memory | Every conversation, plan, and tool call is stored in SQLite and survives a restart |
| Workflow skills | Loads reusable Markdown playbooks from `skills/` through agent tools |

---

## Requirements

- Python 3.11+
- Node.js 18+ (for the Electron/React frontend)
- A ChatGPT account eligible for Codex, an eligible Claude plan for Claude Code, or a provider API key for optional API mode

## Setup

**Backend**
```bash
pip install -r requirements.txt   #  use a venv
```

**Frontend**
```bash
cd frontend
npm install
```

The frontend dependencies include the official Codex CLI. After starting Asrār,
open **Accounts → Sign in with ChatGPT**, finish sign-in in your browser, then
choose **Use in chat**. Device-code sign-in is also available if your account
permits it. Your existing VS Code sign-in remains separate.

The default connection is **ChatGPT subscription**, with read-only workspace
access. Choose a workspace and access mode in **Settings**. Available models
and reasoning efforts come from the connected account. API providers remain
an explicit, separately billed mode; subscription tasks never fall back to them.

For Claude, open **Accounts → Claude Code → Sign in with Claude**, then choose
**Use Claude in chat**. The Python requirements include its SDK and bundled runtime.
See [Claude subscription setup](CLAUDE_CONNECTIONS.md) and
[subscription setup and v0.14 validation](UPGRADE_NOTES_v0.14.md).

**Workflow skills**

Reusable repository workflows live in `skills/*/SKILL.md`. The agent can
discover them with its internal `list_skills` tool and load one with
`use_skill`. Skills provide procedure and guardrails; filesystem, shell, Git,
package, and approval operations still go through the normal agent tools.

The **Skills** panel shows the catalog and full instructions. Names and
descriptions are included in the agent prompt so it can select a relevant
playbook without an extra discovery round trip. Request a particular skill
with `$code-review` or `/code-review`; its body loads before the first model
call. Otherwise the model chooses whether to call `use_skill`.

All 13 bundled skills are adapted for Asrār’s tools. GitHub workflows use an
installed authenticated `gh` CLI; they do not assume Codex or Copilot APIs.

**Optional API mode** — create a `.env` file in the project root:
```bash
ANTHROPIC_API_KEY=...
GROQ_API_KEY=...
GOOGLE_API_KEY=...
DEEPSEEK_API_KEY=...
MISTRAL_API_KEY=...
# Optional, only needed for the paid-tier models in config/models.json:
OPENROUTER_API_KEY=...
QWEN_API_KEY=...
QWEN_API_BASE=...
KIMI_API_KEY=...
KIMI_API_BASE=...
```
You don't need all of them — Asrār only routes to providers whose key is
set. See `config/models.json` for the full model registry and which
provider each one needs.

## Running

**Backend** (from the project root):
```bash
python main.py                  # http://127.0.0.1:8000
python main.py --port 8080      # custom port
python main.py --reload         # auto-reload on code changes, for development
```

**Frontend** (Electron desktop app + Vite dev server):
```bash
cd frontend
npm run dev
```

## Testing

```bash
python -m unittest discover -s tests -v
cd frontend && npx vite build
```

Run a real, read-only API/tool/provider smoke test using a configured key:

```bash
python scripts/smoke.py --model groq-code --timeout 25
python scripts/check_models.py groq
```

The smoke test uses a temporary conversation database, permits only skill
discovery/loading, and verifies the assistant reply was persisted. It makes
real provider requests, so normal provider billing and rate limits apply.
The catalog check reports exact model IDs; provider aliases may still work
without appearing as exact matches.

Routing defaults to `ASRAR_ROUTING_MODE=fast`: local keyword/intent detection
with short-follow-up context. To experiment with a model classifier, set
`ASRAR_ROUTING_MODE=hybrid` and optionally `ASRAR_ROUTING_TIMEOUT=1.5` in `.env`
and restart. The deadline caps at five seconds; manual model selection skips
that request. Local history compaction also avoids a separate LLM call by
default. `ASRAR_SPECULATIVE_RACE=1` enables experimental buffered dual-model
racing, which makes extra requests; it is off by default.

Streaming displays routing and first-token timings. A stalled provider has a
15-second first-event deadline, and transient HTTP errors retry once before
any output. Automatic fallback stops after mutating tool execution to avoid
replaying side effects. These are runtime controls, not an OS sandbox.

See [v0.13 upgrade notes](UPGRADE_NOTES_v0.13.md) for validation, sources, and
remaining limitations.

---

## API

Base URL: `http://127.0.0.1:8000`

| Method | Path | What it does |
|---|---|---|
| `POST` | `/chat` | Send a message, get the full response back (non-streaming) |
| `POST` | `/chat/stream` | Same, but streamed token-by-token over SSE — see event format below |
| `POST` | `/chat/approve` | Resolve a pending destructive-command approval (`{conversation_id, action_id, approved}`) |
| `GET` | `/conversations` | List all conversations |
| `POST` | `/conversations` | Create a new conversation |
| `GET` | `/conversations/{id}` | Get a conversation's full message history |
| `PATCH` | `/conversations/{id}` | Rename/update a conversation |
| `DELETE` | `/conversations/{id}` | Delete a conversation |
| `GET` | `/conversations/{id}/plan` | Current step-by-step plan for that conversation, if any |
| `GET` | `/conversations/{id}/log` | Raw execution log (routing decisions, tool calls, retries) |
| `GET` | `/models` | List the model registry |
| `GET` | `/skills` | List discoverable workflow skills |
| `GET` | `/skills/{name}` | Inspect one skill and its instructions |
| `POST` | `/models` | Add a model to the registry |
| `POST` | `/models/lookup` | Resolve/register a model and test its connection |
| `GET` | `/models/providers` | Supported provider presets and protocols |
| `PATCH` | `/models/{key}` | Update an existing model connection |
| `POST` | `/models/{key}/test` | Test authentication, completion, and streamed tool results |
| `DELETE` | `/models/{key}` | Remove a model from the registry |
| `PATCH` | `/models/{key}/toggle` | Enable/disable a model |
| `GET` | `/tasks` | Task-history endpoint |
| `DELETE` | `/tasks` | Clear task history |
| `GET` | `/debug/test-file-creation` | Sanity-check that write_file works on this machine |
| `GET` | `/debug/test-project-creation` | Sanity-check that create_project works on this machine |
| `GET` | `/debug/check-file/{path}` | Check whether a given path exists |
| `GET` | `/debug/env-keys` | Which provider API keys are currently set (booleans, not the values) |
| `GET` | `/health` | Liveness check |

### `/chat/stream` SSE event format
```
data: {"meta": true, "model": "...", "task_type": "...", "reason": "..."}
data: {"token": "..."}
data: {"tool_start": "tool_name", "args": {...}}
data: {"tool_result": "tool_name", "preview": "...", "success": true}
data: {"diff": true, "path": "...", "unified_diff": "..."}
data: {"approval_required": true, "action_id": "...", "command": "...", "reason": "..."}
data: {"nudge": true, "files": [...]}
data: {"done": true, "tool_calls": [...]}
data: {"error": "..."}
```

---

## Architecture

```
backend/
  api/routes/     FastAPI routes (chat, models, tasks, debug, conversations)
  core/           agent loop, router, classifier, supervisor, hooks, memory, orchestrator
  providers/      one thin adapter per model provider (anthropic, groq, google, ...)
  tools/          what the agent can actually do (files, shell, web, testing, codesearch, ...)
skills/           reusable workflow playbooks (review, CI, Git, sync, troubleshooting, ...)
frontend/
  src/            React + Electron desktop UI
config/
  models.json     the model registry — add a model here to make it routable
tests/            pytest suite covering the agent loop, tools, and orchestrator
```

See [`LAYOUT.md`](./LAYOUT.md) for the annotated file-by-file breakdown, and
[`ASRAR.md`](./ASRAR.md) for the full behavior spec and version changelog.

### Kimi and Qwen account connections

Kimi Code browser sign-in is available in **Accounts & connections**, alongside Codex and Claude Code. Install the Python `kimi-cli` runtime first; see [Kimi connection setup](KIMI_CONNECTIONS.md). Existing Kimi and Qwen API options remain available in API mode. Qwen account OAuth is unavailable according to its current official documentation; the Accounts page includes the notice.

### Meta Muse Code

**Accounts & connections → Meta · Muse Code** supports Meta account sign-in and coding tasks through the official Muse runtime. The chat model selector shows account-available Muse models and reasoning efforts, including `max`. Wonder Full requires an eligible subscription. A separate Meta API provider is also available. See [Muse connection setup](MUSE_CONNECTIONS.md).
