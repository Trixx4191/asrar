---
name: generate-run-commands
description: Find or update project setup, development, build, and test commands from repository configuration.
---

# Generate Run Commands

Read manifests, README files, scripts, and existing task configuration using find_files/read_file. Derive commands from those files rather than guessing. Include working directories and required environment variable names, never secret values.

Explain the commands in chat. When the user requests saved editor tasks, merge them into the existing .vscode/tasks.json using edit_file/write_file without removing unrelated tasks. Asrār has no Codex Run button, worktreeCreated hook, or inAgents task integration. Do not promise those features. Package installation uses pip_install/npm_install and the approval UI; do not install merely to list commands.
