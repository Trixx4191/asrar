---
name: create-pr
description: Create a GitHub pull request for the requested branch and changes.
---

# Create Pull Request

Inspect git_status, git_diff, git_log, the current branch, and remote tracking state. Run relevant checks. Prepare a concrete title and description describing the final changes and validation.

Asrār has no GitHub MCP integration. If gh is installed and authenticated, use run_command with gh pr create; write multiline text to a temporary file and pass --body-file. Use use_skill with name commit for requested uncommitted work. Confirm the intended base branch when it cannot be determined. Publishing a branch or PR must be within the user’s request; never force-push. If GitHub access is unavailable, provide the prepared title/body and explain the blocker.
