# PR #138 custom model provider hardening — 2026-10-16

## Scope and source

Remediation of PR #138 at `f9983baea5e612433cad7e97d5e2213060f04aa2`,
combined with target branch `codex/core-rc18-jvspatial-022` at
`72da747e22ed2f8ca7b6278bec40d32c968d3508`. The target is merged into the
feature branch without rewriting the contributor's history. Core remains a
framework: no App-specific fields, records, domains or commercial policy are
introduced. There are no new dependencies or runtime data migrations.

## Findings resolved

1. **Credential authority binding.** An omitted primary key is retained only
   for the same provider and canonical custom endpoint. A changed provider or
   endpoint requires a new key or `clear_api_key: true`. Rejected transitions
   make no provider request and preserve the saved credential. The Settings
   checkbox explicitly clears the primary secret for a keyless custom service.
   Provider changes also clear unsaved input, including recommended setup.
2. **Outbound network boundary.** One pure URL canonicalizer is shared by API
   schemas, credential persistence and route construction. It rejects embedded
   credentials, queries, fragments and ambiguous URL forms. Public user-selected
   endpoints require HTTPS. The existing operator-owned trusted-host and extra-
   port settings allow deliberate internal services. Probe and inference clients
   validate outbound policy and pin checked DNS answers at actual dispatch;
   redirects and environment proxies are disabled. Failed DNS pinning fails
   closed. Validation requires an OpenAI-shaped models list, caps its response
   at 64 KiB and enforces a 10-second total deadline. Probe error bodies are not logged.
3. **Explicit keyless versus damaged credentials.** Only a record with no stored
   secret receives the keyless SDK sentinel. Nonempty ciphertext that fails
   decryption follows the existing strict/hybrid credential-failure behavior.
   The sentinel prevents SDK deployment-key fallback and is removed from the
   wire. Deployment organization/project IDs are not inherited by the explicit
   custom SDK client.
4. **Usable saved configuration.** Removed a redundant model-selection effect
   that cleared a saved custom model ID when credentials loaded. Endpoint changes
   disable Save until a new key or explicit keyless choice is provided.

## Runtime lifecycle

The custom transport is selected only for workspace BYOK OpenAI routes carrying
an explicit endpoint. Operator-owned deployment routes and existing vendor and
Ollama routing retain their contracts. LiteLLM receives an explicit OpenAI SDK
client with the guarded HTTP transport; mocks of the resolver alone are not the
qualification. Streaming responses own that client through completion, explicit
close and cancellation, including close before the first chunk. DNS pins are
released on ordinary client close and context-manager exit.

## Regression evidence

The tests exercise provider-to-custom and custom-to-custom authority changes,
canonical same-endpoint retention, explicit key clearing, the public API and UI,
malformed/missing/rotated encryption keys, local/private/metadata/IPv6/encoded
addresses, operator-trusted internal hosts, ports, DNS rebinding, failed pinning,
redirect refusal, bounded probe bodies/time and safe error handling. Actual
LiteLLM/OpenAI SDK calls use a mock HTTP transport for keyed and keyless streaming
and non-streaming requests. Tests assert deployment key/org/project isolation and
resource cleanup. A bridge integration test verifies that production dispatch
selects the guarded transport.

## Completed local qualification

- Full default substrate backend selection: **6,688 passed, 933 skipped, zero
  failures/errors** (7,621 outcomes). Domain Apps and slow opt-in tests retain
  the repository's normal default exclusions.
- Final targeted API, service, resolver, network and native SDK selection:
  **182 passed, zero skips/failures/errors**. This includes the final inference
  guards and provider accounting metadata regression cases.
- Native PostgreSQL credential/API/transport selection: **83 passed**, no skips,
  errors or failures.
- Full frontend: **1,832 tests across 321 files passed**. Final model settings
  and speech selection: **10 passed** after the last UI edit.
- TypeScript check and production frontend build passed (existing chunk-size
  warning only).
- CI-faithful smoke selection passed with TESTING=1, DEBUG=false and xdist.
- Core-only lane passed. Staged formatting/lint, mypy and substrate guards passed.

Logs and XML are retained as `pr138-*` under `/private/tmp` and copied into the
local remediation evidence directory at handoff. The separate extension-contract
run and remote CI are reported at handoff rather than assumed to be green here.
This change does not deploy the feature or merge the pull request.
