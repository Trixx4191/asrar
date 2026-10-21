# Muse Code in Asrār

Accounts & connections includes **Meta · Muse Code**. Sign in with Meta, approve the device code in your browser, then choose **Use Muse in chat** and start a new conversation. The button selects Muse Wonder at `max` effort when the account's catalog offers it. You can change the model and effort in the chat toolbar.

Muse Wonder Full appears only when Meta returns it for your account. It requires Power Usage or Maximum Usage. Choosing `max` reasoning on Wonder does not select Wonder Full. Asrār starts its Muse host in Standard mode (`--contributor-mode off`) without changing the stored account setting. Your plan’s Standard-mode limits apply.

The integration drives the official `muse serve` runtime using Muse Session Protocol v1. Account and permission-profile methods are part of its experimental interface, so future runtime versions may require integration updates. No community adapter or additional Python SDK dependencies are required.

## Runtime installation

Asrār discovers `ASRAR_MUSE_BIN`, then a project-local native binary under `data/muse-runtime/bin`, then `muse` on PATH. Set `ASRAR_MUSE_BIN` to an absolute native executable path when using a custom installation. A project-local installation uses the official installer's `.muse-version` marker and verified `muse-bin-<version>` artifact.

The official installer is available at <https://dev.meta.ai/install.sh>. If installing manually, follow [Meta's installation instructions](https://dev.meta.ai/docs/muse-code), then refresh Accounts. The runtime is a native application download, not model weights; models run on Meta's servers. Restart the backend after changing environment variables.

Asrār gives Muse private XDG configuration, data, and cache directories under `data/muse-code`. It strips inherited Meta/API credentials from the child environment and requires the account-login credential lane. Signing out clears the runtime's private account login; API keys and ordinary CLI/IDE accounts are separate. Conversation transcripts remain in Asrār.

## Tasks and permissions

Tasks support streaming, authoritative final replies, resumed sessions, workspace file references, one-time approvals, staged approval updates, questions, stop, and disconnected-stream cleanup. Conversation sessions cannot move between workspace/account connections or other runtimes. Unknown server operations are rejected. The integration does not silently fall back to API billing.

Read-only selects Muse's available native Read-only profile; workspace-write selects Ask me. The automated approval judge is disabled, shell-network mode is restricted or proxy-only according to Asrār preferences, and task startup requires confirmed sandbox availability. Missing profiles or sandbox support stop the task rather than relaxing permissions. Web-search preferences are supplied as instructions; cached and live are not distinct Muse runtime modes here. Multi-select question dialogs are not supported by the current Asrār UI; stop and handle those tasks in Muse Code.

## Separate API option

Meta is also a provider in API models:

- Provider: `meta`
- Model ID: `muse-wonder-1.0`
- Base URL: `https://api.meta.ai/v1`
- Credential variable: `MODEL_API_KEY`
- Protocol: OpenAI compatible

Use API models to register and test it after configuring your own credential locally. API usage is separately billed by Meta. Wonder Full is not an API model. Existing API keys and model entries are preserved.

## Validation

`tests/test_muse_connections.py` exercises a real subprocess running a deterministic fake MSP host. It checks account isolation, browser-flow controls, scoped single-use decisions, authoritative replies, session resume, stream cancellation, host death, access requirements, local-client/origin guards, and supported model/effort choices without provider billing. The installed native Muse 1.4.11 runtime also passed live initialization, private-profile validation, signed-out account status, and permission-catalog discovery with sandbox support available. No model inference was requested. A real account authorization requires the account holder to finish Meta's browser flow.

References: [Meta model availability](https://dev.meta.ai/docs/models), [Muse permissions](https://dev.meta.ai/docs/muse-code/permissions), [official protocol documentation](https://meta-models.github.io/muse-code-sdk/next/).
