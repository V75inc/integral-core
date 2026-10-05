# WP-03.3.1 — Encrypted native turn-input capsule evidence

**Status:** accepted child slice; native WorkItem producer remains disabled.

## Implemented

- Added strict `ChatTurnExecutionContext` fields with an explicit allowlist and a byte-size cap. Prompt and inline image bytes are excluded from this context and remain in the canonical ChatMessage.
- Added `HarnessTurnInputRecord` as an I-GRAPH-02 Object. Capsules use deterministic thread-scoped Object IDs, AES-GCM with Object ID as AAD, a canonical JSON digest, and encrypted tenant/principal/workspace/thread/work/message/request identity.
- Extended `submit_chat_turn` with an optional trusted execution context. In the existing PostgreSQL transaction, WorkItem input receives only accepted message ID, request fingerprint, opaque capsule Object ID and digest. Repeated identical requests reuse one capsule; changed content or context conflicts.
- Added a scoped capsule loader that checks reference, digest, ciphertext, schema version, and identity against the caller's execution scope.
- Added capsule inclusion in generic Harness key rewrap, exact-scope Thread cleanup, and retention that only deletes expired capsules after the linked WorkItem is terminal or missing. Queued/running work is preserved.
- Added optional bounded `client_request_id` to the chat API. The frontend creates one UUID per send and reuses it for a 401 refresh replay; the server stores it with the run metadata. Existing clients remain compatible.
- Native durable chat admission was not enabled and provider defaults were not changed.

## Validation

- PostgreSQL-focused chat submission, input capsule, Harness session cleanup, key rotation, and retention suite: **41 passed**.
- Frontend `JvAgentProvider` auth/replay suite: **5 passed**; request ID equality across the 401 replay asserted.
- `npm run lint:types`: **passed**.
- `make verify`: repository guards, pre-commit checks, backend formatting/lint/mypy, pinned formatting, frontend ESLint (0 errors; existing warning backlog), type check, reproducible wheel (`0dbf791fd07795a02e0a4eba394ab4dda64d0d37878e2c010eb7385d15f8c128`), wheel import, CI-faithful backend smoke, and all **1,304 frontend tests** passed.
- The full backend suite failed the same three recorded baseline tests: two MCP declared-App/workspace scope parity tests and `test_stage_update_entry_prefers_existing_profile_status` (direct stager test lacks a bound principal). No capsule/request-ID tests failed.
- Fresh browser repeat in [`WP-06 browser evidence`](../wp-06/browser-smoke-2026-10-04.md): exact native response, reload persistence, `provider_id: integral_native`, healthy isolated API, and empty browser warning/error collection. Its diagnostic payload again showed `session_id: null` beside a non-null provider session ID; Core session linkage remains unproven.

## Acceptance limits and handoff

- The PostgreSQL test reopened the persistence context before loading the encrypted capsule, establishing durable round-trip beyond the writer's in-memory entity cache.
- Thread-scope rewrap is callable using `thread_scope_key`; the current helper accepts any canonical 64-hex scope key. No automated repository-wide rotation orchestrator exists to enumerate all thread scopes. WP-09 must include thread-scope enumeration/rotation in its operational key-retirement procedure before production use.
- Capsule integration is available to the later producer but is not yet called by the live API. Native WorkItem admission remains blocked on WP-03.3.2 lease/fence/effect controls and WP-03.3.3 worker/replay/recovery.
