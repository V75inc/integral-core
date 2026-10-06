# WP-09.1 evidence — checkpoint manifest and run-fenced pointer

## Implemented

- Added a strict, versioned `HarnessCheckpointManifest` contract and encrypted,
  immutable `HarnessCheckpointManifestRecord` Object storage. Reads bind the
  authenticated payload to the original tenant, principal, thread, session,
  run, snapshot, and schema version.
- The provider records the manifest after a complete framework snapshot. It
  records capability restore policies, plan revision, model request IDs, tool
  receipt IDs, pending WorkApproval IDs, codec version, and usage-completeness
  state. A checkpoint is marked safe only when model requests and tool effects
  are settled and the run has no pending WorkApproval.
- Added `last_checkpoint_run_id` and a PostgreSQL compare-and-set pointer update
  fenced by the session's current Core chat run ID. Missing/unsafe manifests
  and stale run continuations cannot advance the pointer.
- Owner-authorized session export now includes checkpoint manifests; hard
  deletion erases them with the encrypted run state.

## Verification

Command:

```sh
cd backend && INTEGRAL_TEST_DB=postgres \
  JVSPATIAL_POSTGRES_DSN=postgresql://integral:integral@127.0.0.1:5433/postgres \
  .venv/bin/python -m pytest tests/native_harness/wp_05 \
  tests/native_harness/wp_06 tests/native_harness/wp_09 \
  tests/contract/test_harness_sessions_postgres.py \
  -o addopts='' --strict-markers -q
```

Result: **34 passed** against the live PostgreSQL test cluster. The set includes
missing-manifest refusal, encrypted manifest persistence, stale run-fence
  rejection, export, cleanup, manifest-backed restore, recovery guards, and provider continuity. Black,
isort, flake8 on the touched files, `git diff --check`, and
`make verify-core-only` passed.

The repository-configured backend mypy hook passed in the full gate.

## Repository gate

`make verify` passed all 16 substrate guards, pre-commit hooks, pinned
format/lint, backend mypy, reproducible wheel qualification, CI-faithful backend
smoke, and all **1,304 frontend tests across 220 files**. The final full backend
suite failed on the same three non-harness tests recorded in the execution
ledger: two MCP applied-scope expectations and one direct stager test without a
bound principal. The focused PostgreSQL native-harness suite passed.

## Boundaries still open

The current direct-streaming path uses the active Core run ID as its atomic
fence because it does not execute under a WorkItem lease. The manifest is
written after a complete turn; it is not yet written atomically beside every
model/tool/approval boundary. Pending WorkApproval prevents marking the
checkpoint safe, but durable approval resume is still WP-08. App-specific
obligations/artifact expiry and in-flight kill-point reconciliation also remain
open. This receipt does not close WP-09 or establish production readiness.
