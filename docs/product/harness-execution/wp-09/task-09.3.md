# WP-09.3 — Failure boundaries and encryption rotation

**Status:** review

**Dependencies:** WP-09.1 and WP-09.2 implemented; PostgreSQL contract service available.

## Objective

Prove recovery behavior when execution stops at lifecycle boundaries, Core and
Pydantic AI Harness persistence diverge, checkpoint data is corrupt, or the
storage encryption key rotates.

## Owned paths

- `backend/tests/native_harness/wp_09/`
- `backend/tests/contract/test_harness_sessions_postgres.py`
- `docs/product/harness-evidence/wp-09/task-09.3-failure-boundaries.md`
- This task brief and the execution ledger.

## Required behavior

1. Cancellation after Core claims a run but before the first model dispatch
   preserves the run fence and makes no provider call. Existing safe-rebuild
   policy remains limited to a scoped, text-only user turn with no model,
   effect, or approval activity.
2. A framework snapshot without its Core manifest after a provider request is
   not resumed. A Core checkpoint pointer whose Harness snapshot is missing is
   a hard conflict. A complete snapshot/manifest pair written before a lost
   pointer update may repair the pointer and continue.
3. Corrupt or unauthenticated checkpoint ciphertext fails closed; the reader
   must not interpret it as empty history or continue from an older snapshot.
4. With new and previous encryption keys configured, old ciphertext remains
   readable and new checkpoint writes use the current key. A resumable,
   per-session sweep rewraps all Harness payload Objects (including mutable
   plan state through revision/ciphertext CAS) under the current key. Previous
   key retirement is tested only after the sweep verifies all rows are readable.

## Verification

```sh
cd backend
INTEGRAL_TEST_DB=postgres \
JVSPATIAL_POSTGRES_DSN=postgresql://integral:integral@127.0.0.1:5433/postgres \
.venv/bin/python -m pytest \
  tests/native_harness/wp_09 \
  tests/native_harness/wp_04/test_litellm_sdk_transport.py \
  tests/contract/test_harness_sessions_postgres.py \
  -o addopts='' --strict-markers -q
```

Acceptance remains subject to fresh review. The rewrap procedure stops on
corrupt/unreadable rows and can be rerun after a partial completion.
