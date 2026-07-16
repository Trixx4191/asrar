# Asrār — Project Layout

```
asrar/
│
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

**Build order:**
1. ✅ Phase 1 — `core/` (done)
2. 🔜 Phase 2 — `tools/` + `providers/` + `agent.py`
3. 🔜 Phase 3 — `api/`
4. 🔜 Phase 4 — `frontend/`
