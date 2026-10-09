# Configuration guide

The canonical settings live in `backend/app/config.py`, with jvspatial's own settings used at bootstrap. `.env.example` explains local configuration. A real `.env` is private deployment state and must not be committed or pasted into diagnostics.

## Managed local installations

With `integral up`, the launcher prepares storage and credentials automatically. Follow the [local installation guide](LOCAL_INSTALLATION.md) for the complete command reference. `--home` selects the installation directory; otherwise `INTEGRAL_HOME` or the operating system's default data directory is used.

Place optional operator settings in that installation's private `settings.env`, then stop and restart Integral to apply changes. The launcher keeps database access, signing and encryption keys, loopback addresses, real authentication, and production-mode flags authoritative. Its generated runtime configuration uses `INTEGRAL_ENV_FILE` to select one explicit environment file, so an unrelated checkout's `.env` cannot affect the managed server. `INTEGRAL_ENV_FILE` is also available to independently managed deployments; when set, Core reads only that file rather than its usual dotenv locations.

Configure provider credentials through Settings → AI Models. Keep `installation.json`, `settings.env`, and backups private: they contain the credentials needed to preserve access to this installation. Use `integral backup` to capture them together with records, files, and App definitions.

## Core and packages

| Setting | Source default | Meaning |
|---|---|---|
| `DEBUG` | false | Explicit development behavior; production keeps it false |
| `WORKERS` | 1 | Conservative worker count |
| `INTEGRAL_CORE_ONLY` | false | Filter library loading to Core packages; root Compose sets true |
| `INTEGRAL_PACKAGE_PATHS` | empty | Package roots; empty uses configured default discovery locations |
| `INTEGRAL_HOST_EXTENSION_MODULE` | empty | Explicit host-owned extension module |
| `MAX_CONCURRENT_TURNS_PER_USER` | 5 | Ordinary per-worker turn ceiling; not universal shared admission |

Core-only mode and an empty App catalog do not disable agentive boot. `AGENTIVE_ENABLED` is not a current settings gate.

## Native resident and continuity

| Setting | Default | Purpose |
|---|---|---|
| `INTEGRAL_NATIVE_DURABLE_CHAT_ENABLED` | false | Durable native dispatch/replay/recovery rollout |
| `INTEGRAL_HARNESS_CHAT_TURN_TIMEOUT_SECONDS` | 900 | Accepted turn deadline |
| `INTEGRAL_NATIVE_MODEL_REQUEST_TIMEOUT_SECONDS` | 180 | Individual provider request ceiling |
| `INTEGRAL_NATIVE_TURN_TOKEN_LIMIT` | 600000 | Aggregate turn token ceiling |
| `INTEGRAL_NATIVE_TURN_REQUEST_LIMIT` | 20 | Model requests per turn |
| `INTEGRAL_HARNESS_SESSION_RETENTION_DAYS` | 90 | Terminal private execution-record retention |
| `INTEGRAL_NATIVE_MODEL` | unset | Optional server model; workspace model setup works independently |

Configure the model, route, and credential reference according to the selected binding. A default model identifier in Compose is not a supplied credential or a universal provider guarantee.

## Secrets and credentials

`JVSPATIAL_JWT_SECRET_KEY` is the signing authority. OAuth uses `JVSPATIAL_OAUTH_KEY_ENCRYPTION_KEY`. Model credentials use `INTEGRAL_CREDENTIAL_ENC_KEY`, with `INTEGRAL_CREDENTIAL_ENC_KEY_PREVIOUS` supporting the documented rotation path. Keep these stable, private, and recoverable with backups.

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
