# Asrār v0.12 — Power Upgrade Notes

## Implemented

### 1. Hybrid classifier (keyword + model-backed)
- `classify_task()` — fast keyword path (sync, zero latency)
- `classify_task_async(history=...)` — when confidence < 0.55 **or** conversation has prior turns, a fast model (Llama/Mistral/Gemini/Haiku) re-classifies with recent context
- `route_async` + `decide_route_async` wire this into supervisor/routing
- Source field: `keyword` | `model` | `hybrid`

### 2. Richer tool surface
| Tool | Purpose |
|------|---------|
| `git_status` / `git_diff` / `git_log` / `git_commit` | Repo awareness + safe commits (gated) |
| `list_processes` / `inspect_process` / `kill_process` | Process manager (kill gated) |
| `pip_install` / `npm_install` | Package ops (always gated) |
| `semantic_search` | Hybrid token-aware code search (embedding-ready) |
| `rollback_plan` | Restore plan from checkpoint after failure |

### 3. Plan checkpoints + rollback
- `plan_checkpoints` table in SQLite
- Auto-checkpoint before mutating tools (hooks)
- On tool failure: mark in-progress step pending + hint rollback
- `plan_manager.checkpoint_before_step` / `rollback_last` / `rollback_to_checkpoint`

### 4. Provider adapter unification
- `ProviderCapabilities` (streaming, native_tools, vision, quality scores)
- `BaseProvider.stream_complete()` async iterator protocol
- Groq rewritten as reference adapter with full streaming + tools
- Other providers can be upgraded to the same interface incrementally

### Concurrent tools expanded
Read-only set now includes git read tools, process inspect, semantic_search.

## Next (still open)
- True token streaming end-to-end in chat.py (use `stream_complete`)
- Unify agent loop under orchestrator (kill chat.py duplication)
- Context summarization for long conversations
- Speculative dual-model race for fast_chat
- Embedding backend for semantic_search
- Capability auto-detect probes per provider

## v0.12.1 — Unified stream loop + real token streaming

### 1. True token streaming
- OpenAI-compatible providers (Groq, DeepSeek, OpenRouter, Kimi, Qwen) implement
  `stream_complete()` yielding live `token` / `tool_call_delta` / `done` events.
- Shared helper: `providers/openai_compat.py`.
- Anthropic/Google still buffer then emit token chunks (can be upgraded next).

### 2. Unified agent loop — no more chat.py duplication
- New `run_task_stream()` in `core/agent.py` is the single streaming agent loop
  (routing, retry, tools, verification nudge, approval, diffs).
- `/chat/stream` is a thin SSE serializer over that generator.
- Non-streaming `/chat` continues to use `run_task()` (same tool/routing logic).
- Both paths use hybrid `decide_route_async` when conversation history exists.

### Provider capabilities
All providers now declare `ProviderCapabilities` (streaming, tools, vision,
quality scores). Router/orchestrator can use these later for smarter selection.

## v0.12.2 — Context, race, native streams, semantic ranking

### Context summarization
- `core/context.py`: keeps last 8 turns, summarizes older ones (local heuristic always; optional fast LLM).
- Tool results truncated in history; summaries cached in `conversation_summaries` table.
- Wired into both `run_task` and `run_task_stream`.

### Speculative dual-model race
- `orchestrator.race_models()` — first successful non-error response wins; losers cancelled.
- Auto-used in non-streaming `run_task` for `fast_chat` / `summarization` when ≥2 candidates and no force_model.

### Native Anthropic + Google streaming
- Full `stream_complete()` on both providers (token + tool deltas).
- Stream path uses `stream_complete` for **all** providers that support it.

### Semantic search ranking
- Hybrid TF-IDF over candidate lines (token expansion, camelCase/snake split).
- Ranked output with scores; method=`hybrid-tfidf`. Embedding backend can plug in later without schema change.

All planned upgrade items for this pass are complete.
