---
name: update-pr
description: Update the commits or description of an existing GitHub pull request at the user’s request.
---

# Update Pull Request

Identify the PR from the conversation or authenticated gh pr view through run_command. Inspect local changes, branch tracking, and incoming commits. Preserve unrelated work and resolve only understood conflicts. Run relevant checks; use use_skill with name commit when a requested commit is needed.

Push only within the requested update, without force. Update the title/body with gh pr edit when the scope has changed, using a body file for multiline text. Report the PR URL and validation. Asrār does not receive an automatic PR context block; ask for the target when it cannot be determined.
