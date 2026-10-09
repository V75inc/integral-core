# Business model configuration hardening — 9 October 2026

The team identified agent chat failures as a Business environment configuration
issue. The exact failing team configuration was not provided. A greeting in the
existing native Core deployment succeeded before changes.

## Repairs

- Workspace Ollama Cloud keys now use the same fixed cloud endpoint for validation
  and chat, independent of a local daemon environment setting. Cloud chat uses
  typed Ollama chat responses and retains BYOK credential identity.
- Malformed native model routes/generation settings report an actionable setup
  error. Typed outbound provider failures retain specific safe error categories
  through client wrappers, including streaming failures. Host persistence errors
  are not misreported as provider connection failures.
- Managed settings reject malformed, bare and duplicate assignments before
  startup. Persistent keys remain unchanged; authoritative auth/storage settings
  cannot be overridden by operator settings.
- Business instructions prefer the managed launcher and explain desktop
  `settings.env` versus hosted `.env`. Compose requires all three storage/signing
  keys and uses the host gateway for a local Ollama daemon.

## Preserved invariants

Core retains authenticated workspace scope, permission fencing, immutable
credential generation identity, dispatch intent, outcome reconciliation and
billing observations. No tool/effect is replayed as part of error reporting.
I-GRAPH-01/02, I-EXT-01 and the policy/approval/effect authority clauses are
unchanged. No graph structure, App behavior, access grant or resident selection
is introduced by these repairs.

## Qualification

- Targeted model route, transport, classification and credential tests: 82 passed;
  the final SDK transport run, including context errors, passed all 42 cases.
- Managed installation tests: 31 passed. PostgreSQL regressions: 122 passed.
- Business desktop suite: 69 passed. Compose rejected each missing required key
  and rendered the expected local daemon host gateway with valid synthetic keys.
- Browser `Hello` at an unreachable endpoint reproduced the generic error before
  repair, then showed safe model endpoint guidance on the repaired wheel.
- After correcting settings and restarting, greeting/discovery and a read-only
  follow-up succeeded. The assistant reported three tasks; the browser table
  showed those same three records.
- The existing Business desktop was upgraded through the public CLI, retained
  its installation identity and all storage keys, signed in successfully and
  completed a native workspace read-only lookup. Its bundled Core wheel was
  refreshed. All 642 shipped Core Python modules matched the checkout; the
  domain package namespace is intentionally excluded from the wheel.
- Local wheel SHA-256:
  `1b02e8ab96cbe0b3189b8184448830abd7cf46d93e73ac216764a2cd8f4a9034`.
  This is a local candidate; package publication was not performed.
- Full `make verify`: passed, including guards, format/lint/types, wheel import,
  CI-faithful no-developer-env smoke, 1,578 frontend tests and the full backend suite.
- Business desktop CI passed on macOS, Windows and Linux.
- The primary Core deployment at port 9140 was also upgraded; all keys were
  retained and a native read-only workspace lookup succeeded in its browser.

Browser/desktop screenshots and the machine-readable result live in
`/Users/eldonmarks/Documents/Codex/2026-10-09/integral-harness-sweep/`:
`env-endpoint-before.png`, `env-endpoint-after.png`,
`env-recovery-followup.png`, `desktop-env-recovery.png`, and
`env-hardening-results.json`.

The team's exact failing environment remains unknown; the controlled reproduction
proves an environment failure and the repaired diagnosis/recovery, rather than
establishing the team's precise original root cause.
