# Interrupted build and conversation recovery — 2026-10-15

## Scope and deployment identity

The supplied screenshots came from another deployment with no known URL or Core
commit. The supplied logs show successful status polling only; they do not reveal
that deployment's exception. Remote root cause and deployment acceptance remain
unverified.

Local reproduction uses the existing Business preview at `http://localhost:9114`
and Core base `f46d22f7` plus the uncommitted patch identified in
`/tmp/core-recovery-exact-preview.json`. Business source, runtime database volume,
credentials and encryption configuration are preserved. No remote deployment or
merge is part of this change.

The final local preview contains thirteen production files with patch SHA-256
`4b6ea099b64311f5e75962c128a50baf76594e7d3ff59ecf32bd426a603d73b2`.
The successful six-update browser run used the same behavior with a direct
Pydantic type import; the final preview routes that import through Core's existing
compatibility facade.

All changes are generic Core harness, approval, transaction, projection and worker
contracts. No App-specific runtime fields, slugs, workflows or UI were added.

## Reproduced defects and repairs

1. **Plan correction exhaustion interrupted the turn.** The approved-build wrapper
   raised `ModelRetry` beyond the available correction budget. It now returns a
   settled actionable rejection on the last attempt and prevents another dispatch
   that turn. The completion validator reports the actual rejection.
2. **Known pre-effect failures blocked conversation continuation.** Original Core
   validation receipts now reconcile those terminal runs without replay. Unknown,
   foreign, partial and pending effects remain blocked. The audited build errors
   are `invalid_scaffold_plan`, `plan_differs_from_design`,
   `scaffold_plan_validation_failed` and `incomplete_scaffold`.
3. **Name-only shapes were inconsistent.** Explicit title-only seeds now work like
   equivalent unlabelled titles. Tables for declared shapes with no custom fields
   accept/materialize a title column. Required fields and schema-bound columns on
   shapes that have custom fields remain enforced.
4. **Approval closure poisoned its enclosing transaction.** A notifier repeated a
   terminal transition, caught `already_consumed`, and left the joined transaction
   marked for rollback. Matching terminal acknowledgment is now a no-op. Default
   decisions still reject duplicate consumption; identity/kind/state mismatches
   cannot authorize or change a decision. The first nested exception is retained
   as the rollback exception's cause.
5. **Completed host guards had no broker receipt.** Recovery recognizes only the
   original encrypted, paired and completed Core tool returns for
   `build_already_attempted` and `approved_design_immutable`, with original
   capability membership. Model prose or an unresolved effect is insufficient.
6. **Successful writes could lose their conversational return.** An original scoped
   Core receipt can now restore an observation-only return. A past success is not
   current state: the model must read current records and must not replay the write.
7. **Lease checks read stale identity caches.** Fenced effects, heartbeat and
   adapter/output preflight now read current typed WorkItem storage facts.
8. **Long fenced effects blocked their own heartbeat.** The heartbeat now locks the
   matching running lease row before checking expiry. An effect renews its same
   fence before releasing its row lock. Nested renewal is allowed only while the
   same task owns that exact locked authority. Other tasks, changed fences,
   cancellation, malformed/expired outside leases and deadlines still fail closed.
   A crash rolls back and leaves the old lease reclaimable.
9. **Resumed producers reused event ordinals.** Stream keys include the lease fence,
   preserving append-only events across producer generations. Reusing a key for
   different content in the same generation is still rejected.
10. **Graph context failed within transactions.** JV Spatial 0.2.2's transaction handle
    lacks projection methods. A generic compatibility adapter delegates node and
    edge reads to the held transaction, never the parent database. Current access
    policy and all capture budgets remain enforced; compatibility mode is explicit.

## Preserved invariants

- **I-WORK-01/02:** fresh storage, token/fence and same-task transaction ownership
  govern renewal and effects; restoration observes original outcomes and never
  dispatches them again.
- **I-WORK-03/05 and I-APPROVAL-01/02:** approval application remains atomic and
  single-use. A closure acknowledgment cannot grant authority or alter outcomes.
- **I-HARNESS-01:** original scoped, encrypted checkpoints and receipts supply
  recovery evidence; unknown mutations remain unavailable for continuation.
- **I-CONTEXT-01 and I-RET-01:** projection stays within the held transaction and
  current workspace/access limits, including uncommitted reads and rollback.
- **I-SUBSTRATE-01 and I-EXT-01:** all runtime changes are domain-neutral. Personal
  manager records are isolated browser test fixtures, not Core behavior branches.

## Qualification

- Plan/broker/compiler/terminal/worker/transaction focused suite: 222 passed,
  one PostgreSQL-only skip (`/tmp/core-recovery-final-focused.xml`).
- Worker/execution/recovery units after lease changes: 107 passed
  (`/tmp/core-heartbeat-lock-unit.xml`).
- Independent heartbeat, nested lease and authority-change regressions:
  7 passed (`/tmp/core-heartbeat-lock-targeted.xml`).
- PostgreSQL graph suite: 43 passed; transaction read-your-writes/rollback probes:
  2 passed (`/tmp/core-transaction-graph-green.xml`,
  `/tmp/core-transaction-graph-readback.xml`).
- Combined final PostgreSQL suite: 116 passed, zero skipped
  (`/tmp/core-recovery-exact-postgres.xml` and `.log`).
- All staged-index substrate and module guards pass
  (`/tmp/core-recovery-exact-guards.log`).
- Pinned formatting/lint/convention/mypy checks pass on all changed files
  (`/tmp/core-recovery-qualification-hooks.log`).
- Exact-final-source CI-faithful smoke: 1,776 passed, 22 skipped, zero failures
  (`/tmp/core-recovery-exact-ci.xml` and `.log`); TESTING=1, DEBUG=false,
  no backend .env and xdist with the CI smoke marker.
- Earlier broad runs were superseded as additional defects were reproduced.
  A pre-final-source smoke lane passed 1,776 tests with 20 skips
  (`/tmp/core-lease-final-ci.xml`); it is not exact-final-source CI evidence.
  The broader pre-final-source suite had 6,455 passes, 697 skips and one
  compatibility-import guard failure (`/tmp/core-lease-final-full.xml`). That
  import was moved through Core's compatibility facade and the targeted guard
  and terminal recovery suite now pass (93 tests;
  `/tmp/core-recovery-compat-final-green.xml`). The final full run below supersedes that earlier broad result.

- Exact-final-source full default backend suite: 6,456 passed, 701 skipped,
  zero failures/errors across 7,157 tests, in 1,510.91 seconds
  (`/tmp/core-recovery-exact-full.xml` and `.log`). Optional PostgreSQL cases
  remain skipped in this default lane; the separate targeted PostgreSQL lane
  above has zero skips. This does not claim all optional/slow/release gates.
- All twenty-two changed Python source/test files matched the qualification
  hash inventory throughout the final runs
  (`/tmp/core-recovery-exact-qualification-source.json`).

## Browser and persisted-data evidence

- An already-blocked pre-effect rejection continued after reload with a read-only
  explanation and no build replay: `/tmp/core-rejection-recovered-thread.jpg`.
- Minimal design → confirmation → build succeeded and persisted its App, Track,
  table and title-only sample: `/tmp/core-closure-build-passed.jpg`.
- Larger test App: `n.WorkspaceApp.813ff46a6f124072a2249e16`; exactly three Tracks,
  5 Businesses, 6 Expenses and 4 Contacts. Five Expenses link to existing Business
  entries; Coffee is unlinked. Groceries is 120. Baseline IDs and values are in
  `/tmp/core-personal-smoke-before-update.txt`. No duplicate App was created.
- Thread `n.ChatThread.2406741be240440698702635` now continues successfully and
  reports current 5/6/4 counts and Groceries at 120, with no build replay.
  Screenshot: `/tmp/core-recovery-final-readback.jpg`.
- The same thread proposed all six existing-record edits as one approval. After
  approval it finished normally and verified current records with nine tool
  calls. The App and two Track names, two Entry titles and Groceries amount were
  updated exactly. All original App/Track/15 Entry IDs and five typed business
  links were preserved, including the Walmart link after its rename; Coffee
  remains unlinked. Counts remain 5/6/4 and no duplicate App was created.
  SQL assertions: `/tmp/core-six-updates-readback.json`.
  Screenshot: `/tmp/core-six-updates-browser-passed.jpg`.

- On the exact thirteen-file preview, a subsequent read-only run was refreshed
  while active. The UI reattached, the run finished with three tool calls, and
  current readback confirmed all six updates, the 5/6/4 counts and retained
  relations. No new interruption or earlier-run recovery block occurred.
  Screenshot: `/tmp/core-recovery-post-refresh-passed.jpg`.

## Reproduction commands

From `backend/`, with the frozen test/development environment installed:

```sh
python -m pytest -n 4 --dist loadfile
TESTING=1 DEBUG=false PYTEST_XDIST_AUTO_NUM_WORKERS=2 python -m pytest \
  -n auto --dist loadfile --ignore=tests/domain_apps \
  -m "smoke and not domain_app and not slow"
INTEGRAL_TEST_DB=postgres python -m pytest \
  tests/native_harness/wp_03/test_work_item_effect_fence.py \
  tests/contract/test_chat_turn_events_postgres.py \
  tests/contract/test_staging_decisions_postgres.py \
  tests/contract/test_staging_command_apply_postgres.py \
  tests/test_graph_context.py tests/test_transaction_scope.py
```

The PostgreSQL command uses the disposable local test service, not the Business
runtime database. Test results, source qualification hashes and local deployment
patch identity are distinct evidence; none proves the unknown remote deployment
runs this patch.

## Remaining gates

Local implementation, focused/CI-faithful/full backend tests, staged guards and
browser/database acceptance have passed. These changes are being saved as
focused local commits. Remote push, remote CI, release artifact qualification and
acceptance on the unidentified deployment are separate remaining gates.
Do not report the unidentified deployment fixed, arbitrary unknown writes
replayable, or the overall hardening goal complete.
