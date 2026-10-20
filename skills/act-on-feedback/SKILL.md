---
name: act-on-feedback
description: Apply user feedback to an Asrār conversation or the project changes it describes.
---

# Act on Feedback

Use the feedback supplied in the conversation. If the user identifies particular findings, address that subset. Ask for the missing feedback when none is present; Asrār has no session comment API.

Inspect the affected files with search_code/read_file and the current diff with git_diff. Make focused edits using edit_file. Run the relevant checks with run_tests or execute_code, then report which findings were addressed and any that remain. Do not claim to resolve comments in an external app unless an available tool actually did so.
