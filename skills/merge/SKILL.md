---
name: merge
description: Merge a specified branch into its intended target while preserving user work.
---

# Merge Changes

Use git_status/git_log and run_command for git branch and git worktree list to determine the source, target, and worktree. Do not assume a Codex session base branch or main worktree. Clarify an ambiguous target before merging.

Preserve uncommitted work; do not automatically commit unrelated files. Use run_command for git merge in the intended worktree, following the approval UI for gated operations. Resolve conflicts only when both sides’ intent is understood. Do not discard one side wholesale. Run relevant checks and verify the source is an ancestor of the target. Do not push, skip hooks, or rewrite history without an explicit request.
