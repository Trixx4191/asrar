# Asrār — Project Layout

```
asrar/
├── ASRAR.md                        ← Identity & config (start here)
├── .env                            ← API keys (never commit)
├── .env.example                    ← Key template
├── requirements.txt                ← Python deps
├── package.json                    ← Electron/React deps
│
├── config/
│   └── models.json                 ← Live model registry
│
├── backend/
│   ├── main.py                     ← App entry point
│   ├── core/
│   │   ├── classifier.py           ← Task type detection ✅
│   │   ├── router.py               ← Model selection ✅
│   │   ├── registry.py             ← Add/remove models ✅
│   │   ├── agent.py                ← Main agent loop (Phase 2)
│   │   ├── orchestrator.py         ← Circuit breaker, retry/backoff, concurrent tools (v0.11) ✅
│   │   └── memory.py               ← Conversation history (Phase 4)
│   │
│   ├── tools/
│   │   ├── web.py                  ← Search & browse (Phase 2)
│   │   ├── files.py                ← Read/write docs (Phase 2)
│   │   ├── shell.py                ← Run commands (Phase 2)
│   │   ├── testing.py              ← Run test suites / snippets to verify edits (v0.8) ✅
│   │   ├── codesearch.py           ← Grep/glob the project before editing (v0.9) ✅
│   │   └── diagnosis.py            ← PC fault detection (Phase 2)
│   │
│   ├── providers/
│   │   ├── base.py                 ← Base provider class
│   │   ├── anthropic.py            ← Claude calls
│   │   ├── groq.py                 ← Llama via Groq
│   │   ├── google.py               ← Gemini calls
│   │   ├── deepseek.py             ← DeepSeek calls
│   │   └── openrouter.py           ← Any new model
│   │
│   └── api/
│       ├── server.py               ← FastAPI server (Phase 3)
│       └── routes/
│           ├── chat.py             ← /chat endpoint
│           ├── models.py           ← /models CRUD
│           └── tasks.py            ← /tasks history
│
├── frontend/
│   ├── electron/
│   │   ├── main.js                 ← Electron entry
│   │   └── preload.js              ← IPC bridge
│   └── src/
│       ├── App.jsx                 ← Root component
│       ├── components/
│       │   ├── Chat.jsx            ← Main chat UI
│       │   ├── ModelBadge.jsx      ← Shows active model
│       │   ├── TaskHistory.jsx     ← Past tasks
│       │   ├── ModelRegistry.jsx   ← Add/manage models UI
│       │   └── Settings.jsx        ← App settings
│       └── styles/
│           └── main.css
│
└── logs/
    └── actions.log                 ← All agent actions logged
```
Asrār is a local agentic AI desktop application. The backend owns routing,
tool execution, persistence, safety gates, and streaming; the frontend is a
React/Electron client for those APIs.

```text
asrar/
├── main.py                         Backend entry point
├── ASRAR.md                        Agent identity, behavior, and scope
├── README.md                       Setup, API, and operating guide
├── config/
│   └── models.json                 Enabled models, providers, priorities, task map
├── skills/                         Markdown workflow playbooks
│   ├── code-review/SKILL.md
│   ├── commit/SKILL.md
│   ├── fix-ci/SKILL.md
│   ├── troubleshoot/SKILL.md
│   └── ...                         Git, PR, sync, feedback, and run-command workflows
├── backend/
│   ├── api/
│   │   ├── server.py               FastAPI app and SQLite initialization
│   │   └── routes/
│   │       ├── chat.py             Non-streaming chat, SSE chat, approvals
│   │       ├── conversations.py    Conversation, plan, and execution-log APIs
│   │       ├── models.py           Model registry CRUD and lookup
│   │       ├── tasks.py             Action-log history
│   │       └── debug.py             Local diagnostic endpoints
│   ├── core/
│   │   ├── agent.py                LLM/tool loops and tool schemas
│   │   ├── classifier.py           Keyword + model-backed task classification
│   │   ├── router.py               Model selection and fallback chain
│   │   ├── supervisor.py            Sticky routing across clarification turns
│   │   ├── orchestrator.py          Retry, circuit breaker, races, tool concurrency
│   │   ├── context.py               Conversation compaction and summaries
│   │   ├── memory.py                SQLite conversations, plans, logs, approvals
│   │   ├── hooks.py                 Pre/post tool safety and plan hooks
│   │   ├── plan_manager.py          Plan checkpoints and rollback
│   │   └── skills.py                Skill discovery and Markdown loading
│   ├── providers/                   Provider adapters and shared API helpers
│   └── tools/                       Files, shell, web, testing, Git, packages, search, diagnosis
├── frontend/
│   ├── electron/                    Desktop process and IPC bridge
│   ├── src/App.jsx                  Navigation, health, model selection
│   ├── src/components/Chat.jsx      SSE chat, plans, tool activity, diffs, approvals
│   └── src/styles/main.css           Desktop UI styling
├── tests/                           unittest/pytest coverage for tools and agent behavior
├── data/asrar.db                    Runtime SQLite database, created on startup
└── logs/actions.log                 Runtime action and verification log
```

## Request flow

1. The React client sends a message to `POST /chat/stream`.
2. The supervisor and router classify the task and choose an available model.
3. The provider adapter performs the LLM call with native tool schemas.
4. The agent executes tool calls through hooks and the orchestrator.
5. Read-only calls may run concurrently; mutations remain ordered.
6. Tool results, diffs, plans, approvals, and verification nudges stream back as SSE events.
7. The conversation, tool calls, plan state, and execution events are persisted in SQLite.

## Skills

Skills are guidance-only Markdown resources. `backend/core/skills.py` validates
the folder name and frontmatter, `list_skills` exposes the catalog, and
`use_skill` loads a selected playbook into the agent context. The loaded text
does not execute anything; all side effects still use the normal tools and
approval hooks.

## Development commands

```bash
python main.py
cd frontend && npm run dev
python -m unittest discover -s tests -v
cd frontend && npm run build
```

**Build order:**
1. ✅ Phase 1 — `core/` (done)
2. 🔜 Phase 2 — `tools/` + `providers/` + `agent.py`
3. 🔜 Phase 3 — `api/`
4. 🔜 Phase 4 — `frontend/`
