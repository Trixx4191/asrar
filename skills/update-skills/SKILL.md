---
name: update-skills
description: Create or update Asrār workflow skills when the user asks to preserve reusable project knowledge.
---

# Update Asrār Skills

Asrār discovers skills/{name}/SKILL.md in its installation root, not .github/skills or a Codex session directory. Use list_skills and use_skill to check for an existing home before adding a skill. Keep narrow corrections in the relevant existing playbook.

A skill needs YAML-style frontmatter with name matching the folder and a concise description of when it applies. Use read_file/write_file/edit_file to maintain it. Describe outcomes and operational constraints using tools Asrār actually exposes. Do not refer to Codex UI buttons, Copilot logs, unavailable MCP tools, or claim a playbook can grant execution permissions.

Keep names and descriptions cheap to discover. Put lengthy conditional material in references and load it only when relevant. Scripts and references are files, not executable tools; use the normal file/shell tools and approval gates. Check that list_skills discovers the saved skill and use_skill loads it. Report the path and what knowledge was captured.
