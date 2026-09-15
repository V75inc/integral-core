# Security review — BYOK model credentials (2026-06)

## Scope

Per-user LLM API keys for the embedded jvagent harness: storage, API, runtime resolution, audit, and account lifecycle.

## Threats and mitigations

| ID | Threat | Mitigation | Status |
|----|--------|------------|--------|
| T1 | Plaintext key in DB backup | AES-256-GCM with deployment `INTEGRAL_CREDENTIAL_ENC_KEY`; write-only API | Implemented |
| T2 | Key in ChangeEvent / audit logs | `audit_snapshot_credential` — fingerprint only, no ciphertext | Implemented |
| T3 | Cross-tenant key use | Resolve by workspace **owner** auth id, not acting user | Implemented |
| T4 | Concurrent turn key bleed | jvagent `ContextVar` per async task; never `os.environ` | Implemented (jvagent rc10) |
| T5 | Key in HTTP GET responses | GET returns metadata; POST accepts key once | Implemented |
| T6 | Weak dev encryption | Refuse BYOK save when `INTEGRAL_CREDENTIAL_ENC_KEY` unset (non-DEBUG) | Implemented in `upsert_user_credential` |
| T7 | Orphan credentials after account delete | `delete_credentials_for_user` in `user_lifecycle` | Implemented |
| T8 | Validate endpoint abuse | Auth-required; provider ping only; rate-limit via existing API middleware | Partial — rely on deployment rate limits |

## Non-goals (v1)

- OAuth for OpenAI/Anthropic API keys (paste + validate only)
- Per-org credential vault separate from owner BYOK
- KMS envelope / per-tenant DEK (documented for ROADMAP T1)

## Review checklist

- [x] No `api_key` on jvagent `Action` attributes or `agent.yaml` context
- [x] No key material in `ChatTurnContext.extra_data`
- [x] `hmac`/constant-time not applicable (no key comparison in API; provider validates remotely)
- [x] Account deletion purges `UserModelCredential` Object rows

## References

- `docs/backend/model-credentials-byok.md`
- `docs/backend/adr/001-model-credentials-byok.md`
- jvagent `CHANGELOG.md` — per-turn model override (rc10)
