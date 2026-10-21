# Kimi Code account connection

Accounts & connections now includes **Sign in with Kimi**, a browser device authorization flow driven by the official Python Kimi Code CLI. This is separate from the existing Kimi API model provider. Existing Qwen and Kimi model entries, API keys, and API routing remain available in API mode.

Install the Python CLI on the machine running the backend:

```sh
uv tool install kimi-cli
```

The backend must be able to find `kimi` on its PATH. Alternatively set `ASRAR_KIMI_BIN` to its absolute executable path. This integration targets the Python `kimi-cli` ACP interface (`kimi login`, `kimi acp`, `KIMI_SHARE_DIR`), not the newer Go CLI server API. Restart the backend after changing its environment.

Open Accounts & connections, select **Sign in with Kimi**, finish authorization in the browser, then choose **Use Kimi in chat** and start a new conversation. An eligible Kimi Code account is required. Sign-in can be cancelled. Signing out or switching accounts requires stopping active tasks first.

Kimi manages OAuth and refresh tokens under Asrār's private `data/kimi-code` directory. Only account type and a validated official verification URL reach the frontend. Server API credentials are removed from the runtime's environment. Signing out clears only this private runtime directory, including its sessions; it does not change terminal/IDE accounts or API keys. Asrār conversation transcripts remain available.

Chat supports ACP streaming, persisted session resume, attached workspace paths, one-use conversation-scoped approvals, and cancellation of the runtime process group on POSIX. A conversation cannot move between API, Codex, Claude, and Kimi, or between workspace/account sessions. API fallback is never performed. Model choices are discovered from new ACP sessions and refreshed after a task; the account default is available immediately.

Kimi runs local tools using its native permission system. This integration does not provide an operating system sandbox. Read-only denies permission requests for edits, commands, and unknown tool kinds. Network and search restrictions are prompt instructions, not network isolation. The runtime may perform operations that do not request permission according to its own policies.

## Qwen account sign-in

Qwen's current official documentation states that account OAuth was discontinued on April 15, 2026. Accounts shows this limitation and links to the official notice. No nonfunctional OAuth flow or unofficial web-session workaround is added. The existing Qwen API provider remains available.

References:

- [Kimi CLI login and ACP](https://moonshotai.github.io/kimi-cli/en/reference/kimi-command.html)
- [Kimi runtime data locations](https://moonshotai.github.io/kimi-cli/en/configuration/data-locations.html)
- [Qwen account OAuth notice](https://github.com/QwenLM/qwen-code/blob/main/docs/users/support/troubleshooting.md)

Validation uses a deterministic fake CLI: no real credentials, account sign-ins, or model billing.
