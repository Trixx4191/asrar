---
name: fix-ci
description: Investigate and fix failed continuous integration checks using supplied logs or an authenticated GitHub CLI.
---

# Fix CI

Start from the failed job output supplied by the user. For a linked GitHub PR, use run_command with gh pr checks and gh run view --log-failed only if gh is installed and authenticated. If remote logs are unavailable, explain what is missing and reproduce locally using the repository’s documented commands.

Use search_code/read_file to identify the cause, edit_file for focused fixes, and run_tests for meaningful local verification. Report which failures were reproduced and checked. Do not claim remote CI passed based only on local tests. Do not push changes unless requested.
