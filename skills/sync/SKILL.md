---
name: sync
description: Synchronize or publish a Git branch when the user requests pull, push, or upstream synchronization.
---

# Sync Changes

Inspect git_status and run_command with git branch -vv and git remote -v. Determine the intended remote and branch; ask when multiple choices remain. Preserve uncommitted changes rather than silently committing or stashing them.

Fetch the intended remote and check ahead/behind counts. Use a fast-forward pull when possible. Rebase local commits only when requested or clearly appropriate to the user’s workflow; never drop commits or force-push without authorization. For requested publication, use git push -u with the identified remote and branch. Verify tracking state afterward. Communicate through chat when clarification is needed; there is no vscode/askQuestions tool.
