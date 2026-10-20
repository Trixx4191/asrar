# Claude Code subscription connection

Asrār can use Claude Code through Anthropic's official Python Agent SDK and its
bundled CLI. Sign in with an eligible Claude account, such as Pro or Max, to use
subscription-backed tasks. Existing Anthropic API models are a separate connection.

## Connect

1. Install the backend requirements with `venv/bin/python -m pip install -r requirements.txt`.
2. Restart the backend and frontend.
3. Open **Accounts → Claude Code → Sign in with Claude**.
4. Complete the official Claude browser flow. The runtime owns OAuth and its local
   callback. If Claude displays a verification code, enter it in the verification
   field in Asrār's account card; do not paste it into an AI conversation.
5. Choose **Use Claude in chat** and start a new conversation.
6. Select an available model and supported effort, or keep **Account default**.

`ASRAR_CLAUDE_BIN` can point to a compatible installed Claude Code executable.
The pinned Python SDK normally provides its own runtime; no npm Claude dependency
or global CLI installation is required. Runtime discovery and missing-dependency
messages are available before sign-in.

## Tasks and configuration

Claude uses the same workspace, access mode, custom instructions, file context,
activity display, theme, and chat interactions as Codex. Its model and effort
preferences are saved separately. Switching providers, workspaces, or account
sessions requires a new chat. Existing conversations remain readable.

- Read-only mode exposes inspection and planning tools without Bash or file edits.
- Workspace mode enables file edits and terminal tools, with human approvals.
- Direct file paths are resolved, checked against the workspace, and prevented
  from accessing Asrār's private Claude sign-in directory.
- Terminal sandboxing requires platform support and its system dependencies.
  `failIfUnavailable` is enabled and unsandboxed commands are disabled. A missing
  sandbox produces an error rather than an automatic unsandboxed retry.
- Network access follows the workspace network setting. WebSearch/WebFetch are
  exposed only when web search is enabled. Claude Code treats both cached/live
  settings as enabled search; it does not implement Codex's cached-search mode.
- Approvals are scoped to a conversation and turn, expire on task cleanup, and are
  single-use. Questions support choices and free-text answers.
- Replies, tool activity, plans emitted through TodoWrite, direct file-edit diffs,
  and Claude session IDs are preserved. Stop interrupts and closes the SDK task.
- Claude subscription quota percentages are not exposed by this integration.
  Runtime limit failures are shown without inventing usage figures or switching
  to another provider. Claude account limits and enabled extra-usage settings apply.

## Credentials and billing

Asrār gives Claude Code a private configuration directory at `data/claude-code`
using the documented `CLAUDE_CONFIG_DIR` setting. On POSIX that directory is mode
`0700`. Claude Code manages its own credential file or platform credential-store
entry and token renewal. Asrār never reads credential files or copies OAuth tokens
into `.env`, model adapters, or chat history. Account responses expose only the
account type, email, and plan.

The CLI child environment excludes inherited API keys, OAuth-token overrides,
Anthropic endpoints, and cloud-provider selectors. The SDK's merged environment
also explicitly clears those inherited values. Sign-in is constrained to
`claudeai`, and account preflight rejects API/Console/cloud authentication.
Project/user settings and MCP configurations are not inherited into SDK tasks.
Asrār identifies itself as the host application and never impersonates another
Claude client. Switching/sign-out affects this private configuration, not a
standalone Claude login using a different configuration directory.

No automatic API fallback is provided. Anthropic's current guidance allows Agent
SDK, `claude -p`, and third-party usage against subscription limits; eligibility,
quotas, and billing behavior remain controlled by Anthropic. No real signed-in
model task can be verified until the user finishes sign-in.

## Validation and references

The Claude tests use a fake CLI and SDK peer: no subscription or API requests are
made. They cover auth status allowlisting, environment isolation, login/code/cancel/
logout, provider separation, account/workspace changes, model/effort validation,
permissions, questions, diffs, interruption, initialization errors, and local HTTP
access checks. The SDK peer also checks that connect and disconnect happen on the
same task, as required by its cancellation scopes.

Run all backend tests with `venv/bin/python -m unittest discover -s tests -v` and
build the frontend with `npm run build:web` in `frontend`.

- [Using the Agent SDK with a Claude plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
- [Claude Code authentication and separate config directories](https://code.claude.com/docs/en/authentication)
- [CLI auth commands](https://code.claude.com/docs/en/cli-reference)
- [Python Agent SDK](https://code.claude.com/docs/en/agent-sdk/python)
- [Approvals and questions](https://code.claude.com/docs/en/agent-sdk/user-input)
