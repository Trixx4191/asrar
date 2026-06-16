# TODO

- [x] Update backend/api/routes/chat.py streaming fallback-chain and meta correctness.
- [x] Make streaming handle provider HTTP errors without crashing (ResponseNotRead).
- [ ] Phase B: Perfect “add model” integration with env-key management:
  - [ ] Store env_key (or allow null for no-key) in model registry entries.
  - [ ] Extend backend/api/routes/models.py to accept optional env_key (or auto-detect from provider).
  - [ ] Add API to set env keys in .env (and list them dynamically), update backend/api/routes/debug.py.
  - [ ] Update backend/core/router.py / provider availability selection to respect models with null/unknown env_key.
- [ ] Verify backend with `python -m py_compile`.
- [ ] Smoke test: add model via /models/lookup and then /chat/stream.

