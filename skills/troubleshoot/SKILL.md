---
name: troubleshoot
description: Diagnose Asrār routing, latency, skills, tool execution, provider failures, or streaming problems from local evidence.
---

# Troubleshoot Asrār

Use the conversation ID provided by the user. Through run_command, query GET http://127.0.0.1:8000/conversations/{id}/log for routing and skill/tool events, and /conversations/{id}/plan for the plan. GET /skills lists discoverable playbooks. GET /health checks the server. Do not search Copilot event logs or claim there is a Codex session wire log.

Inspect relevant code with search_code/read_file. logs/actions.log records completed tasks, retries, and tool activity; inspect a bounded tail rather than dumping the full log. Do not expose .env values, authorization headers, or private conversation content in diagnostics.

For latency, separate routing_ms, time_to_first_token_ms, and total_ms from stream timing events. Check ASRAR_ROUTING_MODE, provider errors, fallback metadata, and the requested model. Reproduce with a small harmless prompt. Compare streaming and buffered behavior only when the symptom warrants it. Report observed evidence, the fix, and its verification; distinguish configuration/API access failures from code defects.
