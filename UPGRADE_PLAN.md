# Asrār 

Goal: evolve Asrār into a production-quality agentic platform with a clear orchestrator pipeline, pluggable high-quality classifier, robust provider adapters, stronger safety hooks, concurrent streaming tool execution, and full test coverage.

Objectives
- Maintain backwards compatibility for the API surface used by frontend (`backend/api/*`) during migration.
- Incrementally refactor: introduce `Orchestrator` as the new entrypoint while keeping `agent.run_task` as a compatibility shim.
- Improve routing quality: support a pluggable classifier (keyword→ML/NLU), context-aware routing, and richer fallback logic.
- Provider stability: unify adapters with retries, circuit-breaker, rate-limit handling, and transparent fallbacks.
- Tools: make tool execution concurrent where safe; add explicit pre/post hook policies and a permission model for destructive commands.
- Observability: extend execution_log, improve plan tracking, add structured tracing for tool calls and model decisions.
- Testing & CI: add unit and end-to-end tests plus a GitHub Actions workflow.

Planned phases (prioritized)
1. Design & scaffolding (this commit): add `UPGRADE_PLAN.md`, scaffold `backend/core/orchestrator.py` and `backend/core/adapters/`.
2. Introduce Orchestrator: implement `Orchestrator.run()` that composes `Router`, `Supervisor`, `ToolRunner`, `PlanManager` and a `ProviderFactory`. Keep `agent.run_task` delegating to it for now.
3. Provider adapter layer: create `providers/adapter.py` and refactor existing provider modules to use the adapter interface (small, safe changes first).
4. Classifier upgrade: add pluggable classifier interface and include a higher-quality model-backed classifier (optional ML-based) while keeping keyword fallback.
5. ToolRunner & Hooks: make pre/post hook rules pluggable and add a permissions policy engine; add support for concurrent/safe tool execution and streaming partial results.
6. Supervisor & Plans: enhance sticky routing, plan lifecycle, and UI-friendly events; add plan checkpointing and rollback semantics for tool failures.
7. Observability & Logging: structured JSON logs, execution trace IDs, and query endpoints for execution_log and plans.
8. Tests & CI: add `tests/` for core services, a basic integration test that spins the FastAPI server, and a GitHub Actions pipeline.
9. Docs & migration: update `ASRAR.md`, add migration notes and a short README for contributors.

Backward-compatibility strategy
- Keep `backend/core/agent.py` API stable. New orchestrator introduced as an opt-in refactor target; frontend continues to call same endpoints.
- Provide feature flags (env-config) for turning on new behaviors during rollout.

Immediate next steps (I will perform now)
- Create `backend/core/orchestrator.py` scaffold that wraps `agent.run_task` and provides the new orchestration interface.
- Commit `UPGRADE_PLAN.md` (done) and update the internal todo list.

If you approve, I'll begin Phase 2: move coordination logic from `agent.py` into `Orchestrator`, progressively replacing pieces and adding unit tests as we go.

