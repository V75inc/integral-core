# WP-03.1 Evidence — idempotent chat-turn admission

The Core submission service now accepts an authenticated principal/workspace/thread envelope and a bounded client request ID. It derives a scoped deterministic WorkItem key and ChatMessage ID, fingerprints canonical message parts/provider metadata/parent reference, and rejects reuse of the same request ID with changed content. The durable WorkItem contains the opaque accepted-message reference and fingerprint, not the prompt or attachment content.

Admission fails closed unless a transaction-capable graph store is available. Inside one PostgreSQL transaction, WorkItem idempotency arbitrates concurrent submissions, then the service creates or verifies the deterministic user ChatMessage, wires the `ChatThread —CONTAINS→ ChatMessage` edge, and updates thread activity. The request scope is checked against the authoritative thread owner/workspace. Parent references are validated against both the thread cache and the persisted `CONTAINS` edge. WorkItem, initial outbox fact, message Node, edge, and activity update commit or roll back together. The new `chat_turn` kind is contract-only in this slice; no worker handler or live API/frontend producer was added, so the worker cannot claim this kind yet.

Validation used Python 3.14.3, jvspatial 0.1.1, the isolated PostgreSQL 14.17 test cluster, and the repository's existing `postgres_raw_db` fixture. Exact command:

```text
cd backend && INTEGRAL_TEST_DB=postgres JVSPATIAL_POSTGRES_DSN=postgresql://integral:integral@127.0.0.1:5433/postgres .venv/bin/python -m pytest tests/native_harness/wp_03/test_submission_idempotency.py tests/contract/test_chat_turn_submission_postgres.py tests/test_work_items.py tests/test_work_outbox.py -o addopts='' --strict-markers -q
```

Result: **28 passed**. PostgreSQL contracts cover identical sequential and concurrent retries, changed-content conflict, foreign-principal rejection, prompt-free work payload, rooted ChatMessage reachability, and injected failures after enqueue and at the message-edge write proving complete rollback. Pure contract tests cover scoped deterministic identities, fingerprint sensitivity, bounded request IDs, and non-empty message parts. jvspatial emitted 25 existing `asyncio.iscoroutinefunction` deprecation warnings from its Walker implementation; no test failed.

The focused WP-03.1 tests and work-kernel regressions pass. This evidence does not qualify live API admission, worker dispatch/execution, cross-process cancellation, billing reservation, or changing the native provider default; these remain downstream work.
