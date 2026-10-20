---
name: code-review
description: Review code changes for concrete bugs, regressions, and missing verification when the user requests a review.
---

# Code Review

Use git_status and git_diff to establish the requested scope, then read surrounding code and callers with search_code/read_file. Prioritize correctness, regressions, security, and useful missing coverage. Explain each actionable finding with its file, line, consequence, and suggested correction in the chat response. Asrār has no addComment or inline review API.

Keep review read-only unless the user also asks for fixes. Report evidence and uncertainty; do not invent findings to fill a quota.
