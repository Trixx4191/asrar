---
name: commit
description: Commit requested project changes with a message matching the repository history.
---

# Commit Changes

Inspect git_status, git_diff (including the staged diff), and git_log. Preserve unrelated changes. If changes are already staged, commit those; otherwise stage the files belonging to the requested work with git_add. Avoid secrets, runtime databases, generated output, and virtual environments.

Choose a concise message matching recent commit conventions and explain the intent of the change. Use git_commit; its stored approval must be accepted through Asrār’s approval UI. Never use confirmed=true to grant yourself permission. Verify with git_status and git_log.

Do not amend, discard changes, skip hooks/signing, push, or rewrite history unless the user requests that action.
