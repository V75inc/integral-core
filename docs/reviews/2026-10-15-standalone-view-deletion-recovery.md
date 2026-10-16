# Standalone View deletion recovery candidate

Date: 2026-10-15. Status: focused, full backend, CI-faithful and public artifact qualification
passed; patch integrated for commit on the continuation branch. No merge or
deployment is included. This is not full-goal closure.

## Baseline and source isolation

Checkout `/private/tmp/core-view-delete-recovery`, branch
`codex/standalone-view-recovery`, starts at committed `5b6b6d16` plus an exact
copy of the model-edit/dependency candidate staged tree
`cf164fc586220e7512669794baa1a8ca6de92a39`. The original checkout remains
unchanged while its full backend suite runs. The incremental patch is retained
at `/tmp/core-view-delete-increment.patch`; it must be regenerated if this
candidate changes. Neither checkout is the immutable local browser snapshot.

## Reproduction

Native probes reproduced missing durable deletion evidence after token
completion loss (`/tmp/core-view-delete-red.xml`), receipt failure not joining
the deletion outcome (`/tmp/core-view-delete-rollback-red.xml`), and a
separate-connection membership removal completing while an approved deletion
was paused before its write (`/tmp/core-view-delete-revocation-red.xml`).

## Implemented contract

- The shared `services/view_deletion.py` boundary resolves the current canonical View
  and Track location, enters identity/membership admission, then rechecks that
  location before the exact approved-payload effect fence.
- The existing API policy and scope checks remain authoritative inside the
  admitted callback. Both direct HTTP and approved agent deletion enter this
  boundary; the executor continues to call the same API handler.
- View deletion, manifest synchronization, staged event outbox and minimal
  encrypted staging receipt commit together on PostgreSQL. Direct HTTP deletion
  joins the existing generic resource event outbox to its graph transaction;
  it does not invent an approval receipt. A malformed handler result
  or failed receipt rolls back local changes. Event delivery follows commit.
- Recovery returns only the already-applied deletion fact and opaque execution
  receipt. It never reads a potentially reused View identity, historical title
  or current configuration to reconstruct a deleted result.
- Mixed save/delete batches use the existing private child references and
  stable receipts after child or parent completion loss.

## Focused evidence

- Fifteen native PostgreSQL cases: zero skips, failures or errors,
  `/tmp/core-view-delete-shared-native.xml`. This supersedes the initial
  twelve-case staging-only qualification.
- Seventy-seven related JSON CRUD, scope, and staging cases: zero skips,
  failures or errors, `/tmp/core-view-delete-shared-json.xml`.
- Configured formatting, lint, convention and mypy hooks passed,
  `/tmp/core-view-delete-shared-hooks-final.log`; all 16 staged structural
  guards passed (`/tmp/core-view-delete-shared-guards.log`). Independent Core,
  SDK and external App artifact proofs passed, including reproducible wheel,
  isolated imports and clean installation
  (`/tmp/core-view-delete-shared-artifacts.log`).

These cases cover the three reproduced failures, post-commit event delivery,
malformed result rollback, opaque recovery after membership removal, refusal
to read a reused identity, three mixed-batch interruption points, a changed
selected View after approval, and membership removal before dispatch. The
expanded cases also reproduce and fix the direct HTTP revocation race
(`/tmp/core-view-delete-http-red.xml`), verify HTTP event delivery after commit,
and verify HTTP graph rollback when outbox insertion fails.

## Open qualification and scope

The candidate passed its full default backend suite: 6,378 passed, 619 skipped,
zero failures/errors (6,997 total; 1,288.471 seconds,
`/tmp/core-view-delete-full.xml`). Its subsequent two-worker CI-faithful smoke
selection passed (`/tmp/core-view-delete-ci.log`). The patch is integrated on
the continuation branch; its four runtime/test files match the qualified
isolated candidate byte for byte. GitHub CI on the eventual pushed commit is a
separate gate. This increment
integrates standalone View deletion through both staging and direct HTTP. Other
View mutation entrypoints remain part of the API/native acceptance audit. It does not provide cross-process
transaction guarantees on JSON/memory or unqualified adapters, close the other
executor families, or prove downstream event delivery recovery.

All changes are generic Core contracts. No App-specific fields, labels,
commercial logic or domain modules are introduced.


The reproducible qualified wheel SHA256 is
`804d32ec164cb0714db97349de96d5c0b9d6628fcd0a0ef8ca2e2543d4149130`.
The original model/dependency candidate and subsequent audit diagnostics remain
separate commits. The new operation-output admission work is not part of this
View deletion patch or its qualification receipts.
