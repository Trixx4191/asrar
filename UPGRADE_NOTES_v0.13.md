# Asrār v0.13 — Skills, latency, and runtime reliability

Validated on 2026-10-20. This is a tested runtime upgrade, not a claim of
superiority over Claude, Codex, or Grok. Comparative quality requires a
shared task set, grading, repeated trials, and cost/latency measurements.

## Skills integration

The earlier integration already exposed `list_skills` and `use_skill`, but
several imported playbooks assumed Codex/Copilot UI controls, comment APIs,
GitHub MCP, or Copilot event logs. Discovery alone did not make those
procedures executable in Asrār.

- Rewrote all 13 bundled playbooks for Asrār’s actual tools and approval UI.
- Supply compact skill names/descriptions on each turn. The main model
  selects relevant instructions through `use_skill`; no skill classifier
  request or blanket loading of every skill body is needed.
- Explicit `$skill-name` or `/skill-name` invocation loads that skill before
  the first model call and records a `skill_loaded` execution event.
- Added `/skills`, `/skills/{name}`, and a Skills panel to inspect the same
  catalog the agent sees.
- Reject malformed frontmatter, invalid names, traversal, and skill files
  whose resolved path escapes the installed skill root.

Example: `$code-review review my current changes` loads the review playbook.
An ordinary review request lets the model select `use_skill` itself.

## Routing and calls

- Default local intent/keyword routing avoids a pre-answer model request.
  Keyword matches respect word boundaries; leading operations such as
  “summarize this report” take precedence over incidental topic nouns.
- Short ambiguous follow-ups retain recent task context; clarification
  routing checks that its saved model remains enabled.
- Manual selection skips model classification and explicitly rejects an
  unknown, disabled, or unconfigured model rather than silently switching.
- Hybrid classification remains opt-in with a bounded deadline. Local
  conversation compaction is the default; optional model summaries are
  bounded. Experimental speculative racing is off by default.
- Buffered and streamed calls use the provider adapters, removing a second
  set of provider-specific HTTP implementations in the agent.
- Groq uses the shared OpenAI-compatible transport. Added the missing
  Mistral adapter so its registered models can actually be called.
- Streams preserve Claude native tool blocks and Gemini function calls,
  results, and function-call thought signatures across tool iterations.
  Gemini uses native system instructions and JSON Schema declarations.
- Streaming emits routing, first-token, and total timings. Transient HTTP
  failures retry once only before output; a first event must arrive within
  15 seconds. SSE responses disable proxy buffering.

## Reliability changes

- Model-supplied `confirmed=true` no longer grants execution approval.
  The API claims the stored action once and creates a server-side capability
  scoped to that exact tool and arguments. Permission fields are removed
  from the public tool schemas.
- Failed verification leaves code changes outstanding. Finishing with dirty
  verification state appends an explicit warning; exhausting tool rounds
  reports an incomplete task.
- Provider fallback stops after a mutating tool has run, preventing a fresh
  model attempt from replaying side effects.
- Tool errors and unknown tools report failure consistently. React and
  other common source files can now be read by the file tool. This project’s
  verification selects unittest rather than requiring an uninstalled pytest.
- Tool IDs correlate streamed results with the correct UI entries. The final
  response replaces intermediate text in chat. The API persists the final
  assistant message before emitting `done`, preventing refresh races.
- Provider errors retain status codes and useful messages with API key
  redaction. Google credentials are sent in a header rather than the URL.

## Validation and provider state

- All 13 skills pass the skill-creator frontmatter/naming validator.
- All 117 regression tests pass. Coverage includes routing deadlines, manual selection, skill
  loading, real API routes, stored approvals, tool-turn protocols, stream
  retries/deadlines, failed verification, and prevention of mutation replay.
- The Vite production build passes. Electron’s GUI and installer packaging
  have not been exercised in this environment.
- Groq’s authenticated catalog no longer listed `llama-3.3-70b-versatile`.
  That entry is disabled; the registry adds verified available
  `openai/gpt-oss-20b` (`groq-fast`) and `openai/gpt-oss-120b` (`groq-code`).
  Coding, diagnosis, shell, and document routing prefer `groq-code`;
  summarization prefers `groq-fast` so the default path does not first hit
  the known Anthropic credit failure.
- Both Groq models completed a real `use_skill(code-review)` round trip.
  The 20B run took 3.85 s; a 120B run took 2.52 s. These are single smoke-test
  observations, not stable latency percentiles or coding quality benchmarks.
- A subsequent complete API → agent → provider → tool → SSE → SQLite test
  passed on 120B, including persisted assistant content: routing 19.72 ms,
  first answer token 3426.36 ms, total 3447.41 ms. Provider/network/model
  generation dominates that request; the test includes two model calls.
- Claude’s live test returned insufficient Anthropic credit. Its native tool
  history is regression-tested, but a live completion needs account credit.
- Google’s catalog no longer listed Gemini 2.0 Flash. Updated `gemini-flash`
  to catalog-listed Gemini 3.8 Flash. Its first live request exposed an
  incompatible tool schema, now fixed. Subsequent live runs hit connection
  timeouts or the smoke deadline, so a successful live Gemini tool round trip
  remains unverified. The unit suite verifies schema and signature handling.
- Other providers have not been live-certified by this pass. Registry
  membership or a nonempty API key is not proof of credit, valid credentials,
  tool support, availability, or a free endpoint.

Repeat checks from the project root using the installed environment:

```bash
venv/bin/python -m unittest discover -s tests -v
venv/bin/python scripts/smoke.py --model groq-code --timeout 25
venv/bin/python scripts/check_models.py groq
cd frontend && npx vite build
```

The smoke test restricts tools to discovery/loading and stores the test
conversation in a temporary database. It makes real provider requests.

## Design references

- [OpenAI: Build skills](https://learn.chatgpt.com/docs/build-skills): cheap
  metadata discovery followed by loading only selected instructions.
- [Anthropic: Scaling Managed Agents](https://www.anthropic.com/research/managed-agents):
  distinct session, harness, and execution interfaces. This upgrade improves
  adapter boundaries and persistence; it does not implement hosted sandboxes
  or the full Managed Agents architecture.
- [xAI: Function calling](https://docs.x.ai/developers/tools/function-calling):
  explicit schemas and completion of tool results before continuing the loop.
  Asrār still runs independent reads concurrently and mutations in order.
- [Google: Thought signatures](https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures):
  preserve signatures on the function-call parts that generated them.

## Remaining boundaries

This pass does not add an OS sandbox, credential vault, MCP client, durable
crash-resume engine, or a new autonomous agent team. Local subprocesses still
have the backend’s privileges; substring approval rules are not an OS security
boundary. Plan rollback restores checklist state, not changed files.

Settings behavior toggles remain local UI preferences and are not wired into
server policy. Electron still uses the development Vite URL; packaged loading
and bundled Python distribution need a separate desktop release pass.

The next evidence-driven step is an agent evaluation set covering multi-file
edits, test repair, recovery, and instruction adherence across configured
models, then repeated success, latency, and cost scoring against competitors.
