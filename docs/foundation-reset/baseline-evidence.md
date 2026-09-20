# Foundation reset: baseline evidence

**Captured:** 2026-09-20
**Source revision:** `75a0f35c2d4308b268fda0d8b15775ce9fbcacae` with the active foundation-reset and agent-guidance changes uncommitted.
**Status:** Baseline only. A passing check here does not qualify a release.

## Completed checks

| Check | Result | Interpretation |
|---|---|---|
| `make verify-core-only` | Pass | Core import/profile boundary and Core-only test lane pass. |
| `make verify-ci` | Pass with normal local socket/DNS access | CI-faithful smoke lane passes. The sandbox-only run could not bind the local SSRF fixture or resolve DNS; this was environmental, not a product failure. |
| `tests/contract/test_app_operations_idempotency.py` | Pass | Current duplicate-key cache behavior is covered. |
| `tests/test_work_kernel_lifecycle.py` and `tests/test_work_outbox.py` | Pass | Current work-kernel posture and transactional outbox behaviors are covered. |

The CI run reports existing dependency warnings from Pydantic settings and Starlette's deprecated `TestClient`. They are recorded for normal dependency maintenance; neither was treated as proof of a Core failure.

## Architecture findings confirmed by inspection

1. `app_operations.idempotency` retains an in-process cache and treats durable lookup/store failures as warnings. Its receipt is written after the operation handler returns. This is useful retry behavior in the present runtime, but it is not a durable, concurrent, atomic command receipt.
2. The work kernel already probes for a PostgreSQL transaction handle with compare-and-set and insert-if-absent primitives. Its outbox/approval units are the right consolidation candidate; a second work or approval subsystem must not be introduced.
3. Typed App operations currently resolve policy, validate input, execute a handler, then construct evidence. The next design spike must prove whether one transaction can include the handler's record/edge writes, the operation receipt and the outbox fact.
4. Agent-guidance migration is complete for the two repository `CLAUDE.md` files: both were consolidated into scoped `AGENTS.md` files and removed. All remaining active `CLAUDE.md` references were updated.

## Gate for WP-03

Do not claim transactional command idempotency, remove legacy paths or migrate a protected App operation until a Postgres integration spike proves all of the following with independent callers and injected failure:

- exactly one command claim wins for the same logical operation identity;
- the domain record/relationship mutation, audit/outbox fact and durable receipt commit together;
- a losing or interrupted caller cannot report a successful effect;
- retrying a completed invocation returns the stored receipt, while reusing the key with changed input is rejected.

If the current jvspatial transaction API cannot carry the required writes, record that as a blocking decision and extend the supported persistence adapter deliberately. Direct driver imports or a process-local fallback are not acceptable substitutes.
