# ADR 001 — Dual-mode LLM credentials (platform + per-user BYOK)

**Status:** Accepted
**Date:** 2026-06

## Context

Integral embeds jvagent in-process. Model API keys today are deployment env vars (`OPENAI_API_KEY`). Some tenants want users to supply their own keys; org workspaces should bill AI usage to the workspace owner.

jvagent explicitly forbids persisted `api_key` on model actions (security ADR C2).

## Decision

1. **Hybrid default** — `INTEGRAL_AGENT_KEY_MODE=hybrid`: use workspace owner's BYOK when configured, else platform env keys.
2. **Strict opt-in** — `byo_strict` disables platform fallback; `/agentive/status` returns `model_key_required`.
3. **Kill switch** — `platform_only` ignores stored BYOK.
4. **Storage** — `UserModelCredential` as jvspatial `Object` (I-GRAPH-02), AES-256-GCM at rest with `INTEGRAL_CREDENTIAL_ENC_KEY`.
5. **Runtime** — jvagent `per_turn_model_override` ContextVar (`bind_model_override`) set in `jvagent_harness` for each turn; plaintext never in graph or turn logs.
6. **Resolution** — `billing_user_id = workspace_owner`; org members use owner's key when owner configured BYOK.
7. **OAuth rejected** for API keys — paste + validate-on-save + provider console deep-links.

## Consequences

- Requires jvagent **0.1.2** (dual-provider BYOK shipped in 0.1.0rc11+).
- Owners must understand org members consume their BYOK in shared workspaces.
- KMS envelope encryption deferred to ROADMAP T1.

## Alternatives considered

- Integral-only `LanguageModelAction` subclasses — rejected; orchestrator hard-codes driver classes.
- `os.environ` overlay per turn — rejected; concurrent turns race.
- `UserModelCredential` as `Node` — rejected; no graph traversal need (I-GRAPH-02).

## Amendment — voice input (`speech` slot), 2026-09

Chat dictation and `integral_transcribe_audio` need a speech-to-text key. It
lives on the same credential, as a fifth slot beside default / light / heavy
/ vision, so there is one key-governance model instead of a second store.

1. **Slot.** `speech_provider`, `speech_model`, `speech_api_key_enc` and
   `speech_key_fingerprint` on `UserModelCredential`.
   - Opt-in: an empty `speech_model` means off. Members' dictation bills the
     owner, so it is never on by default.
   - The provider must be speech-capable: `SpeechProvider` in
     `schemas/model_credentials.py`, `openai` only today.
   - The slot reuses the default key when the provider matches, and stores
     its own key otherwise. Turning the slot off drops that key.
2. **Resolution.** `resolve_speech_credential(workspace_id)` follows decision
   6 (owner's key) and the same three modes. There is one extra gate: the
   platform key backs speech only when `SPEECH_ALLOW_PLATFORM_KEY=true`,
   because dictation on it bills the deployment for every workspace.
3. **Browser path.** The raw key never leaves the server. The backend mints
   a short-lived, transcription-only client secret per dictation session,
   and the browser talks to the provider directly (WebRTC). Guests never
   mint. See [speech-input.md](../speech-input.md).
4. **Non-LLM vendors** (Deepgram, AssemblyAI, …) don't fit a model
   credential. They are reserved for a connector-catalog `kind: service`
   (ADR-011, not yet written); the adapter contract already carries a
   `credential_source` for it.
