# Model connections

Asrār can register and configure models through chat, the Models panel, or
the model API. Model registration uses supported adapters and connection
metadata; it does not generate or execute new Python provider code.

## Add models through chat

Examples:

> Add model openai/gpt-oss-20b using provider groq, with strengths fast_chat.

> Add https://openrouter.ai/anthropic/claude-haiku-4.5 through OpenRouter.

> Configure model claude-haiku to use native provider anthropic and model ID claude-haiku-4-5, then test it.

For a custom deployment, provide its exact model ID, API base, protocol, and
the **name** of its credential variable, for example `PRIVATE_AI_API_KEY`.
Enter the key value in the local `.env` file, not in chat.

The agent has four dedicated tools:

| Tool | Result |
|---|---|
| `list_models` | Registered models, credential presence, last validation, provider presets |
| `register_model` | Resolve/register connection, create missing empty credential placeholder, update routing strengths, test the connection |
| `configure_model` | Repair an existing ID, provider, endpoint, credential variable, protocol, or routing, then test |
| `test_model` | Check current credentials, model completion, and a streamed tool-result exchange |

Registration already tests the connection; the agent should not repeat the
test after a ready result. A test makes up to two small provider requests
under a 12-second overall deadline. The test tool itself cannot edit files
or run commands: its only permitted function returns a constant check result.

An existing matching connection is reused. Duplicate keys with different
settings require an explicit configuration update. Ambiguous catalog names
return candidates rather than registering the first substring match.

## Provider configuration

Supported protocols are `openai_compatible` (chat completions), `anthropic`
(Messages), and `google` (Generate Content). Presets include Anthropic,
Google, Groq, DeepSeek, Mistral, OpenRouter, Qwen, Kimi, Together, Cohere,
OpenAI, and xAI. A compatible new provider needs configuration rather than
a separate adapter module. Other API protocols still need an implemented
adapter; registration rejects invented protocol names.

Per-model connection fields are stored in `config/models.json`:

```json
{
  "id": "exact-provider-model-id",
  "provider": "private-ai",
  "protocol": "openai_compatible",
  "api_base": "https://your-provider.example/v1",
  "api_key_env": "PRIVATE_AI_API_KEY",
  "strengths": ["coding"],
  "priority": 5
}
```

The credential value stays out of the registry, model tool arguments, and
model-list responses. Missing credential variables get an empty `.env`
placeholder without changing existing values. A process-provided secret is
preserved instead of being masked by a new empty placeholder.

HTTPS is required for remote connections; localhost HTTP is allowed for
compatible local servers. API bases cannot contain embedded credentials,
queries, or fragments. An optional `PROVIDER_API_BASE` setting overrides
that provider's saved endpoint. `api_key_env` can select distinct credentials
for different deployments.

Gateway IDs preserve the organization prefix. In particular,
`anthropic/claude-...` from OpenRouter uses **OpenRouter** authentication and
endpoint. A native Anthropic ID such as `claude-haiku-4-5` uses Anthropic.
A Claude-branded model can be served by different companies; their keys
are not interchangeable.

## Credential changes and Claude diagnosis

Restart the backend once to load this code upgrade. Afterward, each new
provider connection reads current `.env` settings. A changed key replaces
stale credentials and resets the provider's circuit breaker. The local file
is authoritative over an older process environment value, including an
intentionally empty value. Removing a previously observed file key does not
resurrect its old dotenv value from the process environment.

Use **Models → Test** after changing a key. A prior validation is marked
stale when the local settings file changes. Credential presence is shown
separately from successful authentication and tool use.

Claude's direct-endpoint checks on 2026-10-20 returned
`Provider HTTP 401: invalid x-api-key` with the configuration read at test
time. This differs from the earlier insufficient-credit error. Check which
provider issued the key: use a native Anthropic key for native Anthropic,
an OpenRouter key for OpenRouter, or the issuing gateway's endpoint for a
gateway key. The test errors are redacted; never paste key values into chat.

## Routing and status

Registration inserts a model into the supplied task strengths. Higher
priority ranks first among those candidates; manual selection remains
available. Removing a model also removes its task-map references.

Validation statuses distinguish `ready`, `missing_credentials`, `failed`,
`timeout`, `tool_calling_unverified`, `untested`, and a displayed `stale`
result after settings edits. A failed/timed-out/tool-incompatible model is
excluded from automatic routing. Fix it and test again; manual selection
is still available for diagnosis. A configured key alone does not establish
credit, authentication, model availability, or native tool compatibility.

The Models panel supports provider selection, custom endpoints, and per-model
tests. Chat registration refreshes the model picker without requiring a view
change. Empty, truncated, and error-bearing compatible streams are treated
as failures rather than successful empty answers.

## API

| Endpoint | Purpose |
|---|---|
| `GET /models` | List connections and current configuration/validation state |
| `GET /models/providers` | Supported provider presets and protocols |
| `POST /models/lookup` | Register using `name_or_url` plus optional provider/connection fields |
| `POST /models` | Register an exact ID and configuration |
| `PATCH /models/{key}` | Update specified connection fields |
| `POST /models/{key}/test` | Validate the current connection |

The registration endpoints validate by default. An API client can pass
`"validate": false` to save an untested connection. Raw `api_key` fields are
rejected; use `api_key_env`. A saved row is not reported as ready unless its
probe passes. Registry updates use atomic replacement and serialize writes
within the backend process.

## Repeatable validation

```bash
venv/bin/python -m unittest discover -s tests -v
venv/bin/python scripts/smoke_models.py
```

The live smoke test asks a real model to register GPT-OSS 20B through Groq.
It uses a temporary registry, private temporary credential file, and temporary
database, and restricts tools to that registration and its validation. It
does not add a test model to the live registry.

The final live run called `register_model`, passed completion plus streamed
tool-result validation, and returned `MODEL_REGISTRATION_OK` in 5.68 seconds.
That is one integration observation, not a model-quality benchmark or an SLA.

Provider reference checks: [Cohere compatibility API](https://docs.cohere.com/v2/docs/compatibility-api)
and [xAI REST API](https://docs.x.ai/developers/rest-api-reference/inference).
The presets supply connection defaults; each model's actual compatibility
still depends on its live probe.

Native OpenAI chat requests use `max_completion_tokens`, following the
[current Chat Completions reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).
Compatible services that use `max_tokens` retain that parameter. Responses-only
models need a Responses adapter and are not made compatible by changing a URL.
