# Configuration guide

The canonical settings live in `backend/app/config.py`, with jvspatial's own settings used at bootstrap. `.env.example` explains local configuration. A real `.env` is private deployment state and must not be committed or pasted into diagnostics.

## Managed local installations

With `integral up`, the launcher prepares storage and credentials automatically. Follow the [local installation guide](LOCAL_INSTALLATION.md) for the complete command reference. `--home` selects the installation directory; otherwise `INTEGRAL_HOME` or the operating system's default data directory is used.

Place optional operator settings in that installation's private `settings.env`, then stop and restart Integral to apply changes. The launcher keeps database access, signing and encryption keys, loopback addresses, real authentication, and production-mode flags authoritative. Its generated runtime configuration uses `INTEGRAL_ENV_FILE` to select one explicit environment file, so an unrelated checkout's `.env` cannot affect the managed server. `INTEGRAL_ENV_FILE` is also available to independently managed deployments; when set, Core reads only that file rather than its usual dotenv locations.

Configure provider credentials through Settings → AI Models. Keep `installation.json`, `settings.env`, and backups private: they contain the credentials needed to preserve access to this installation. Use `integral backup` to capture them together with records, files, and App definitions.

The launcher validates its saved installation identity before starting. A missing, damaged, or invalid existing key stops setup with recovery guidance; rerunning `integral up` never replaces that key. Restore the original `installation.json` from this installation's backup before retrying. An empty or damaged existing file is not a fresh installation.

## Core and packages

| Setting | Source default | Meaning |
|---|---|---|
| `DEBUG` | false | Explicit development behavior; production keeps it false |
| `WORKERS` | 1 | Conservative worker count |
| `INTEGRAL_CORE_ONLY` | false | Filter library loading to Core packages; root Compose sets true |
| `INTEGRAL_PACKAGE_PATHS` | empty | Package roots; empty uses configured default discovery locations |
| `INTEGRAL_HOST_EXTENSION_MODULE` | empty | Explicit host-owned extension module |
| `MAX_CONCURRENT_TURNS_PER_USER` | 5 | Ordinary per-worker turn ceiling; not universal shared admission |

For commercial deployments, see the [commercial host integration guide](../platform/commercial-host-integration.md) for extension startup, entitlements, quota/usage hooks, and recovery. `INTEGRAL_SUBSCRIPTION_REQUIRED` is a host-owned Business setting, not a Core switch that disables commercial App checks.

Core-only mode and an empty App catalog do not disable agentive boot. `AGENTIVE_ENABLED` is not a current settings gate.

## Native resident and continuity

| Setting | Default | Purpose |
|---|---|---|
| `INTEGRAL_NATIVE_DURABLE_CHAT_ENABLED` | true | Durable native dispatch/replay/recovery; requires PostgreSQL transaction support. Set false only to opt into legacy request-bound turns. |
| `INTEGRAL_HARNESS_CHAT_TURN_TIMEOUT_SECONDS` | 900 | Accepted turn deadline |
| `INTEGRAL_NATIVE_MODEL_REQUEST_TIMEOUT_SECONDS` | 180 | Individual provider request ceiling, including initial stream output |
| `INTEGRAL_NATIVE_MODEL_MAX_ATTEMPTS` | 3 | Total model attempts including the initial request (1–3); transient failures before output only |
| `INTEGRAL_NATIVE_TURN_TOKEN_LIMIT` | 1200000 | Aggregate turn token ceiling |
| `INTEGRAL_NATIVE_TURN_REQUEST_LIMIT` | 20 | Model requests per turn |
| `INTEGRAL_HARNESS_SESSION_RETENTION_DAYS` | 90 | Terminal private execution-record retention |
| `INTEGRAL_NATIVE_MODEL` | unset | Optional server model; workspace model setup works independently |
| `INTEGRAL_NATIVE_API_BASE` | unset | Deployment custom OpenAI-compatible endpoint for the `INTEGRAL_NATIVE_MODEL` route (Ollama routes stay on `OLLAMA_API_BASE`) |

Configure the model, route, and credential reference according to the selected binding. A default model identifier in Compose is not a supplied credential or a universal provider guarantee.

`INTEGRAL_NATIVE_TURN_TOKEN_LIMIT` sums input and output across all model calls
in one chat turn, including repeated/cached context and history-summary calls.
It is separate from the model's per-request context window, output ceiling,
request/tool count and turn deadline. A larger allowance permits more aggregate
work but does not repair repeated queries or oversized tool responses. See the
[token-budget investigation](qualification/token-budget/2026-10-12.md) for
reproductions, context repairs and workload qualification guidance.

## Secrets and credentials

`JVSPATIAL_JWT_SECRET_KEY` is the signing authority. OAuth uses `JVSPATIAL_OAUTH_KEY_ENCRYPTION_KEY`. Model credentials use `INTEGRAL_CREDENTIAL_ENC_KEY`, with `INTEGRAL_CREDENTIAL_ENC_KEY_PREVIOUS` supporting the documented rotation path. Keep these stable, private, and recoverable with backups.

For source and Docker setup, run `./scripts/bootstrap_env.sh .env .env.example` before starting. Bootstrap generates missing or explicitly placeholder keys once, preserves valid keys (including quoted assignments and inline comments), and refuses malformed or duplicate existing assignments without changing them. Root Compose also requires the storage key explicitly: the native harness encrypts its private checkpoints even when the selected model needs no API key.

`DEBUG=true` alone does not provide a storage key. The local development fallback additionally requires `INTEGRAL_CREDENTIAL_ALLOW_DEBUG_DERIVE=1` and a signing secret. Prefer generated independent keys; changing a derived signing secret also changes the key needed to read previously saved state.

If chat reports that storage encryption is not configured, correct the deployment configuration and restart. If saved AI state cannot be authenticated, verify the original key and backup before taking recovery action. Generating a replacement key cannot unlock existing ciphertext. Neither error should be treated as a pending user confirmation.

`INTEGRAL_AGENT_KEY_MODE` defaults to `hybrid`; supported modes include `byo_strict` and `platform_only`. Inspect the binding's route resolver for precise workspace-owner and provider behavior. Never assume a browser-selected provider can override host policy.

## Files and speech

| Setting | Default | Meaning |
|---|---|---|
| `ATTACHMENT_MAX_UPLOAD_BYTES` | 500 MiB | Individual upload limit |
| `ATTACHMENT_MAX_BATCH_BYTES` | 1 GiB | Batch byte limit |
| `ATTACHMENT_MAX_BATCH_FILES` | 10 | Batch count limit |
| `ATTACHMENT_EXTRACTED_TEXT_MAX_BYTES` | 10 MiB | Extracted text bound |
| `ATTACHMENT_AGENT_TEXT_MAX_CHARS` | 20000 | Agent-facing extracted text bound |
| `ATTACHMENT_SCANNER` | noop | No screening assurance until a real scanner is configured |
| `CHUNKED_UPLOAD_ENABLED` | false | Explicit resumable-upload rollout |
| `SPEECH_ALLOW_PLATFORM_KEY` | false | Platform-key fallback for speech |

Speech and connector providers have their own settings and origins. Update all applicable CSP header sources when enabling a browser transport. Do not expose a long-lived provider key to the browser.

## Change discipline

Review environment changes as deployment changes. Confirm effective non-secret settings, restart components that read them at boot, and verify the affected feature. Use bounded diagnostics that redact keys, tokens, stored credentials, and private conversation content.

## Model setup without environment guesswork

Integral AI is always active. Prefer **Settings → AI Models** for user model
setup. Ollama Cloud credentials bind to `https://ollama.com` for both validation
and chat; `OLLAMA_API_BASE` cannot reroute a saved cloud key to a local daemon.
Ollama Local profiles use that operator-owned variable, defaulting to
`http://localhost:11434` for a direct API and the host gateway in local Compose.

Managed CLI/desktop installations read `<home>/settings.env`, not a repository
`.env`. Use `integral status --home <home> --json` to confirm the installation.
Restart after changing operator settings. Docker/manually hosted APIs use their
explicit `.env` configuration. A deployment default model must use
`provider/model`. No legacy harness opt-in flag is needed.

Malformed model routes and Ollama generation settings produce a model setup
message. Known outbound SDK authentication, connection, timeout, quota and model
errors produce specific safe messages. Core persistence and authority errors
remain distinct. These diagnostics neither expose provider secrets nor replay
uncertain model requests; existing reconciliation and accounting apply.


### Native model retries

The default allows three total physical attempts for a transient provider failure
or an empty reply before output is released. Backoff is 0.5 and 1 second. The chat
shows `Retrying · 2 of 3` / `Retrying · 3 of 3`, then resumes its normal activity.
A retry stays within the same run and repeats only that model request; it does
not restart the agent or replay completed tools. Each attempt has a distinct
request identity, durable dispatch/outcome observations and fresh model admission
where the run uses Work admission. Unknown provider billing outcomes remain
unknown and may retain funding holds; retries are not guaranteed to be free.

Authentication, permission, context limits, admission/storage failures and user
cancellation are not retried. Once text, reasoning or tool output has started,
a later stream failure is surfaced rather than replayed. The existing overall
turn budget still applies. Set the attempt limit to `1` to disable automatic
model retries. This mechanism does not resubmit an entire failed chat HTTP turn.


### User-configured OpenAI-compatible endpoints

Settings → AI Models supports `custom_openai` with a model ID and base URL.
Public endpoints require HTTPS. Private services require an exact operator-owned
`INTEGRAL_OUTBOUND_TRUSTED_HOSTS` entry and, for nonstandard ports, an explicit
`INTEGRAL_OUTBOUND_EXTRA_PORTS` entry. These are deployment decisions; users
cannot grant themselves access to the server's internal network. Validation and
inference both enforce outbound policy, pin checked DNS addresses, disable proxy
environment fallback and refuse redirects. The `/models` validation probe has a
10-second total deadline and a 64 KiB response limit.

Saved credentials are bound to their provider and canonical endpoint. Changing
either requires a new key or the explicit **Use without an API key** option
(`clear_api_key: true` in the API). Blank key input retains the current key only
at an unchanged endpoint. Keyless inference does not forward deployment provider
keys, organization IDs or project IDs. A saved encrypted key that cannot decrypt
is a credential failure, never an implicit keyless selection. Endpoint URLs may
not contain credentials, query parameters or fragments because they are stored
as ordinary metadata.

`INTEGRAL_NATIVE_API_BASE` is a separate operator-owned deployment route; it does
not grant arbitrary user endpoints permission to reach internal services.
