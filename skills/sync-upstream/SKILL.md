---
name: sync-upstream
description: Bring the current branch up to date with a specified upstream base, preserving the intended changes.
---

# Update Branch from Upstream

Inspect git_status, git_log, branch tracking, and remotes using available Git tools and run_command. Identify the actual base from the request or repository; never assume origin/main. Preserve uncommitted work.

Fetch the target remote. Rebase only within the user’s requested scope, or use a fast-forward when possible. Resolve conflicts by preserving the intent of both upstream and local work. Ask about incompatible behavior; do not use a blanket upstream-always-wins rule. Run the relevant checks and report the resulting branch state. Do not publish rewritten history without explicit authorization.
