# rc28 release qualification

Candidate: `0.1.1rc28`, PR #162. Source baseline: rc27 main
`e0b83606`; feature/documentation head before the release update:
`f8cc623e5a26b8deaecb4931ba81f0dfefd0f675`.

## Scope and provenance

This candidate contains App-owner configuration, durable reviewed collection
updates, reduced Track-loading scans, permission-aware semantic passage indexing
and retrieval, local citation repair and the refreshed documentation contracts.
See the [collection/performance evidence](../../qa/2026-10-20-owner-batch-performance.md)
and [semantic qualification](semantic-search-2026-10-20/REPORT.md) for actual
measurements, source identities and limits. Earlier rc27 and semantic-source
measurements remain attributed to those sources, not a new rc28 wheel.

TestPyPI contained rc27 and no rc28 when checked on 20 October 2026. The package
metadata, root package lock entry, CLI fallback, Docker package assertion and
image-qualification assertion now agree on rc28. Runtime dependencies are
unchanged. Source metadata does not prove publication.

## CI findings corrected

Two PostgreSQL fresh-interpreter approval tests inherited `test_in_memory`, a
vector driver registered only in the parent test process. Their child setup now
explicitly selects the null vector adapter while preserving the real PostgreSQL
graph and durable approval/receipt checks. The real semantic lane independently
uses actual encoding and pgvector.

The legacy pgvector driver's collection-time connectivity probe previously
looked at the graph test database before session bootstrap created it. It now
prefers the explicitly provisioned `SEMANTIC_TEST_DSN` and fails if that requested
store is unavailable, preventing a silently skipped real-driver module on fresh
CI runners. Service health checks identify the configured database user.

## Local release-update checks

- Frozen lock check (`uv lock --check --offline`): passed; dependencies unchanged.
- Focused real PostgreSQL/encoder/driver, fresh-process approvals and transaction
  rollback: **51 passed**, with no skips.
- Reproducible rc28 wheel and isolated installed import: passed. SHA256:
  `dbfc7de742cf39f5fc004822f39c3b702620e84402b53d80102711f7b0296709`.
- Pinned pre-commit and substrate guards are required before commit; local
  CI-faithful smoke and exact-head GitHub CI must pass before merge.

The local real database remains PostgreSQL 14. GitHub's PostgreSQL 16 lane,
frontend build/tests, backend checks and independent artifacts are separate
merge gates. Publication requires successful CI on the exact merged main commit
and the normal TestPyPI workflow. No tag/index artifact or deployment is implied
by this qualification record.
