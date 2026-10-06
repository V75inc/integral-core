# WP-02.2 Evidence — encrypted scoped StepStore

**Result:** accepted for encrypted scoped persistence and PostgreSQL idempotency/CAS behavior. This is a package slice result, not whole-harness release qualification.

The Core StepStore encrypts run, event, tool-effect, and checkpoint payloads with AES-GCM and binds query identity to tenant, principal, thread, and session. The scoped wrapper maps framework identifiers and validates every returned record. Reusing a run, event, or checkpoint identity with different content raises `HarnessPersistenceError`; exact retries remain idempotent. The Core Planning store encrypts session-local plan state and mutates it with database revision CAS.

The isolated PostgreSQL 14.17 contract module passes seven tests covering transactional session activation and rollback, concurrent session-generation contention, Root-to-ChatThread-to-HarnessSession traversal, encrypted run/event/checkpoint round-trip, cross-principal isolation, concurrent idempotent writes and conflict detection, owner-scoped session export and deletion, and concurrent PlanStore additions without lost items. WP-02 unit tests pass alongside these PostgreSQL probes. The probes use concurrent database transactions from tasks in one process; they do not qualify multi-host runtime fencing.

Black/isort checks on touched files, flake8 on `harness_sessions.py`, graph-contiguousness, service-layer, Core-import, and skill-compliance guards pass. `make verify-core-only` passes. The full backend suite reports three unrelated failures in the ledger: two MCP applied-scope assertions and one direct stager test without a bound principal.

**Still open at WP-02 package level:** public export API, configurable checkpoint retention, migration/upgrade behavior, encryption key rotation, and multi-host runtime fencing. Do not describe the whole WP-02 package as production-ready from this evidence.
