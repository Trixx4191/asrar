# Asrār

> *أسرار* — Arabic for "Secrets / Mysteries"

A local agentic AI assistant. It routes each task to the strongest available
free model, plans multi-step work, edits files with reviewable diffs, runs
its own tests before calling a task done, and asks for real approval before
touching anything destructive.

Full behavior spec and version history: [`ASRAR.md`](./ASRAR.md).
Codebase map: [`LAYOUT.md`](./LAYOUT.md).

---

## What it does

| Capability | |
|---|---|
| Multi-model routing | Classifies each task and picks the strongest free model for it, with a fallback chain if one fails |
| Agentic tool loop | Reads/writes files, greps the codebase, runs shell commands, browses the web, up to 10 iterations per turn |
| Self-verification | Runs the project's tests (or a quick script check) after code edits, before saying a task is done |
| Codebase awareness | Searches file contents and filenames before editing, instead of guessing at structure |
| Diff review | Shows the exact unified diff for every file edit, not just "done" |
| Human-gated approval | Destructive shell commands need a real Approve/Deny click — the model can't self-certify consent |
| Circuit breaker + retry | Backs off a provider after repeated failures; retries transient errors before falling back to a worse model |
| Concurrent tools | Runs independent reads (search/grep/list) in parallel instead of one at a time |
| Persistent memory | Every conversation, plan, and tool call is stored in SQLite and survives a restart |

---

## Requirements

- Python 3.11+
- Node.js 18+ (for the Electron/React frontend)
- At least one provider API key (see below) — the free tier covers most of it

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

**API keys** — create a `.env` file in the project root:
```bash
ANTHROPIC_API_KEY=...
GROQ_API_KEY=...
GOOGLE_API_KEY=...
DEEPSEEK_API_KEY=...
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
pip install pytest --break-system-packages
python -m pytest tests/ -v
```

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
| `POST` | `/models` | Add a model to the registry |
| `POST` | `/models/lookup` | Resolve a model key to its config |
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
frontend/
  src/            React + Electron desktop UI
config/
  models.json     the model registry — add a model here to make it routable
tests/            pytest suite covering the agent loop, tools, and orchestrator
```

See [`LAYOUT.md`](./LAYOUT.md) for the annotated file-by-file breakdown, and
[`ASRAR.md`](./ASRAR.md) for the full behavior spec and version changelog.
