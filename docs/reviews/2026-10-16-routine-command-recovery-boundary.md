# Routine approval and worker recovery boundary — 2026-10-16

## Current source and qualification status

Work continues directly on `codex/core-rc18-jvspatial-022` in
`/private/tmp/core-hardening-136`, based on pushed `18ea5ef0` (qualified tag and existing-byte filing increments). PR #142 merged at
`22d381a3` after its backend, frontend, native PostgreSQL and artifact jobs passed.
Its Docker job was skipped by the normal pull-request condition. The original
preview remains on `b1e4c67a`; this work did not redeploy or restart it.

The four routine commands below are an **uncommitted candidate**. Their native
component checks have passed, but full, CI-faithful, complete native and artifact
qualification on final combined source remain required. The whole hardening goal
and R1/R2 remain open. An implementation count is not a completion percentage.

## Reproduced boundaries

- All eight initial PostgreSQL probes failed: create/edit/cancel/remove could
  commit without an exact applied receipt after lost completion, and receipt
  storage failure did not prevent their effects. The tests inspected actual
  created/paused/cancelled/deleted state before asserting receipt presence.
  Evidence: `/tmp/core-routine-command-red.xml` and `.log`.
- Five further native probes failed: cancelled/removed routines retained
  preflight/effect authority, and the actual agent route received no parent
  WorkExecutionContext. Evidence: `/tmp/core-routine-work-continuation-red.xml`.
- Six stale-progress probes failed: failure, skipped-run and auto-pause saves
  overwrote later owner instructions or revived a cancelled routine. Evidence:
  `/tmp/core-routine-progress-stale-red.xml`.

Scheduled-fire deduplication (`routine:<id>:<slot>`) is separate from approval
command identity. `source_staging_token` alone is not an effect receipt.

## Implemented candidate

| Boundary | Current behavior |
| --- | --- |
| Create/edit | Exact approved payload, current identity/workspace, canonical private conversation catalogue and unique routine-owner edge; graph mutation, event intent and encrypted applied receipt share the native transaction. |
| Cancel/remove | Same receipt kernel and fresh owned row. Current owner can stop a schedule after workspace execution access is revoked or its conversation is archived. Stopping does not grant workspace reads or renewed execution rights. Removal preserves conversation and private completion evidence. |
| Interrupt ordering | Commit stopped/removed state and receipt before offering local interruption. A failed receipt cannot interrupt an otherwise rolled-back routine. Offers run outside the root graph transaction; joined transactions defer them. Recovery never repeats the stop. |
| Local worker identity | The registry stores trusted routine and WorkItem identities. A matching origin label alone cannot cancel a human turn, another routine sharing the thread, or an unknown legacy run. |
| Agent handoff | The existing in-process agent route receives immutable task-owned server WorkExecutionContext. Client body fields cannot supply it, and another task cannot inherit it as authority. The route uses the deterministic work run identity and reuses only an exactly scoped live run; a terminal prior run requires reconciliation. No unnecessary child queue was added. |
| Worker effects | Scheduled preflights and native effect transactions re-read bound routine state. Effect locks order identity before routine rows, matching stop/edit. A committed stop/removal refuses later effects across pools. Already-admitted effects may commit before a waiting stop; they cannot be recalled. |
| Worker terminal state | A routine lease stopped by current state transitions to cancelled through its current lease fence. It is not left running for repeated recovery, and stale leases cannot terminalize a newer attempt. |
| Progress serialization | Owner edits, failure/skip/auto-pause, successful-run bookkeeping and due advancement lock and hydrate fresh routine rows. Progress updates do not replace later owner edits or revive stopped/deleted routines. Due advancement rechecks current active/due state under the lock. Events/notifications are offered afterward. |
| Receipt readback | Return current authorized schedule state, or the recorded applied/deleted fact with unavailable content withheld. A receipt is not current read authority and never authorizes replay. |

The JSON adapter retains single-process semantics. It does not acquire native
rollback or distributed guarantees. In-flight provider calls may run until their
next cancellation/publication boundary or timeout; a stopped routine is not
proof that an external provider recalled an already-dispatched request.

## Evidence

- Creation/edit expanded native selection: 17 passed, including same-approval
  and distinct-delta races through independent pools.
- Creation/edit plus tag/release selection on the merged branch: 36 passed,
  before the later worker/control/progress changes.
- Four-command and worker-control selection: **40 passed**, zero skips/errors/
  failures, 69.87 seconds (`/tmp/core-routine-four-commands-final-native.xml`).
  Includes the original eight receipt probes, foreign conversation refusal,
  revocation before/after commit, later-state recovery, two-pool edits/claims,
  two-pool stop observation, held-effect/stop ordering, post-commit caller
  cancellation, no local interruption on receipt failure, own stop after lost
  workspace access/archived conversation, worker terminalization, forged-body
  refusal and exact live-run reuse. The stream-construction probe uses a
  synthetic stream producer; it does not qualify a paid model/provider run.
- Fresh-state progress probes: **6 passed** after the row-scope repair
  (`/tmp/core-routine-progress-stale-fixed.xml`). This run followed the 40-case
  selection; rerun their combined final source rather than adding old results
  into a claimed single aggregate gate.
- Combined final native routine/tag/release/work-kernel/effect-fence selection:
  **98 passed / 1 non-native deselected**, zero skips/failures/errors, 166.50
  seconds (`/tmp/core-routine-progress-combined-native.xml`). This run includes
  the repaired progress seam and the command/worker cases together; overlapping
  earlier component counts must not be added to it.
- Current focused scheduling, API, admission, stream outcome, reminder,
  write-scope fail-closed and binding selection: **97 passed / 1 native-only
  skipped** (`/tmp/core-routine-progress-focused.xml`, 50.45 seconds).
- Earlier routine CI-faithful run had two reminder-test failures because a
  conversational fixture stored `perimeter-owner` without an AuthUser. The two
  write-applying tests now use a real isolated AuthUser bound to their rooted
  profile. Admission was not relaxed. Their corrected probe passed 2/2 and
  they are included in the current focused suite. That earlier CI run is not a
  passing qualification of this candidate.

Pinned hooks passed before the newest progress seam; rerun changed-file hooks,
staged substrate guards, full/CI-faithful and independent artifact gates on final
source before committing. Existing broad runs in the older continuation and
filing checkouts qualify only their respective sealed sources.

## Remaining contracts before closure

1. Qualify the full final routine/Entry/native lease matrix, default suites,
   concurrent due selection, completion races and independent-process recovery.
   Continue testing literal reminders and actual agent/provider execution
   separately; synthetic route construction is not provider acceptance.
2. Qualify the replacement for scheduled write-scope reconciliation across
   the full gate matrix. The legacy queue scan is now a no-op. Three native
   probes first reproduced unauthorized application of human cards minted
   mid-run, including after cancellation and removal of the routine's grant
   (`/tmp/core-routine-proposal-authority-red.xml`, 3 failures). The candidate
   moves exact proposal execution into the live capability broker transaction.
   Private task-owned provenance binds the worker, attempt, actor, workspace,
   conversation, capability and effect to the queued mandate fingerprint.
   Configuration edits invalidate queued authority; progress fields do not.
   Unsupported commands, destructive commands and outbound tool save hooks
   retain explicit review. A typed target is selected by command contract,
   never inferred from arbitrary extra payload keys. Legacy unproven queued
   work requires reconciliation rather than receiving a backfilled grant.
   Encrypted execution evidence retains the original proposal and provenance;
   repeat observation returns current authorized state without restaging.
   State-only terminal decision markers retain no payload. Full process-death,
   cross-attempt recovery and all outbound effect families remain unqualified.
   Do not claim all scheduled writes or exactly-once provider runs are closed.
3. Qualify durable due-slot advancement/dispatch, completion/run-budget
   accounting and failure recovery together. Fresh row serialization fixes
   stale overwrite, but alone does not prove exactly-once fire accounting if a
   scheduler dies between advancing a slot, enqueuing work and recording its
   completed run. Preserve existing fire idempotency and receipt ownership.
4. Qualify command revision/migration, expiry, stale approved input, outer
   transaction event delivery, deleted/withheld disclosure and recovery under
   unknown authorization/storage outcomes. Never infer an absent effect from
   unavailable receipt storage.

Affected invariants: I-GRAPH-01 retains named owner/catalogue paths;
I-GRAPH-02 retains private encrypted execution Objects; I-CRUD-01 keeps shared
mutations in services; I-WORK-01/03/05 retain live lease authority, atomic local
units and exact approval identity. No App-specific code or commercial policy was
introduced. No commit, push or deployment includes this candidate yet.


The attachment-filing increment was qualified independently and pushed as
`18ea5ef0` while this routine candidate remained preserved. Its addition to the
combined baseline requires a fresh native combined-source regression before a
routine commit; the 98-case result above precedes that filing integration.

## Broker provenance qualification in progress

- Native exact-proposal and refusal selection: **8 passed**, 25.27 seconds,
  `/tmp/core-routine-origin-negative.xml`. Includes the three former scan
  failures, one applied broker proposal with current-state recovery and changed
  input refusal, wrong typed target, removed grant, changed instruction and
  ordinary human approval retention.
- Focused scheduling, worker, broker and operational-build selection:
  **185 passed / 1 native-only skipped**, 54.78 seconds,
  `/tmp/core-routine-origin-focused-v2.xml`. Three added admission cases
  refuse changed instructions, removed grants and missing legacy mandates
  before the model boundary.
- Private task/scope binding, legacy intent compatibility and frozen mandate
  configuration tests: **3 passed**, `/tmp/core-routine-origin-unit.xml`.
- Earlier full suite in the continuation checkout failed three tests whose
  synthetic write fixtures lacked current AuthUser or canonical conversation
  ownership. The current fixtures now use real isolated owners and catalogue
  edges. The earlier run is failed evidence, not current qualification.
- Full current-source suite is running; no routine commit is qualified yet.
  Re-run combined native, CI-faithful, hooks, guards and artifact gates on the
  sealed final source before committing.


## Final-source qualification follow-through

The first combined routine/filing/provenance native selection passed **136 tests
with one non-native deselected**, 454.67 seconds
(`/tmp/core-routine-mandate-combined-native.xml`). The final candidate adds a
native proof that cosmetic persistence cannot upgrade an existing human card's
immutable origin. It also removes saved summary/diff fields from recovered
public proposal results; only current authorized readback is returned.

Two further broker probes reproduced receipt borrowing after operation-class
or connector changes (`/tmp/core-broker-idem-extra-red.xml`, two failures).
RunStep now records the actual operation class, and terminal reuse verifies
actor, workspace, capability/source, operation class, receipt category,
connector, App and canonical input. Old receipts lacking exact operation-class
proof are refused rather than silently given new identity. The broker,
execution-run and provenance selection passed **45 tests**
(`/tmp/core-broker-idem-class.xml`). A connector read-to-execute probe covers
the case where both operations otherwise have the same environment category.

Final candidate source is sealed in
`/tmp/core-routine-mandate-sealed-source.sha256`. Explicit PYTHONPATH points to
this checkout for the final full and native runs; the shared editable test venv
alone can resolve an older source when launched from the repository root.
Pinned hooks pass. Full, final native and independent artifact gates are
running under the `core-routine-mandate-sealed-*` evidence prefix. The earlier
full-v2 run qualifies the source it collected before the last broker/privacy
edits and cannot substitute for the sealed-source run. No routine commit has
been made. CI at the pushed baseline `18ea5ef0` is green; Docker verification
was normally skipped, so no new image-build result is claimed.


Final candidate pinned hooks and staged substrate guards passed. All four
independent artifact gates passed together (`/tmp/core-routine-mandate-sealed-artifacts.log`):
reproducible Core wheel/import, clean public-metadata ASGI installation,
independent SDK wheel and external App asset registration. Repeated wheel SHA256:
`8052685a4a2d2800276fcc035eecaca19643800f61a4527d50b75e5e3101fd41`.
The full and final native suites are still running; these artifact results do
not substitute for their terminal results or CI at a future routine commit.


## Next durable-fire accounting seam (not implemented by this candidate)

Inspection of `run_scheduler_pass`, `_run_user_batch` and `_execute_run` confirms
three distinct commits: cadence advancement, later WorkItem enqueue, and later
successful-run bookkeeping. A process death after advancement can lose the
one-shot dispatch. `_execute_run` currently derives its slot from the already
advanced row plus mutable progress fields, so this is not proof of immutable
original due-slot identity. A committed worker success followed by scheduler
loss can also miss budget accounting. Fresh row locks do not close these gaps.

The next increment must retain the original due slot while locking the current
routine, enqueue its exact WorkItem in the same native transaction as cadence
advancement, and dispatch that saved WorkItem identity rather than deriving a
new fire from the advanced schedule. Recovery should use the existing durable
WorkItem/worker, not a second schedule journal. Native completion should account
one successful fire and its event intent atomically with that WorkItem's
terminal transition. Routine progress must not revive stopped state, overwrite
owner edits or be applied twice by a later worker. Open-ended and finite
routines need explicit success-count semantics; max-runs completion must be
qualified against concurrent fires and edits. JSON mode remains single-process.

Required fault probes: death/rollback before and after slot enqueue/advance;
two scheduler pools claiming one due slot; completion before/after budget
update; duplicate terminal observation; cancellation and instruction/mandate
changes; finite run-budget races; literal reminder recovery; and independent
process restart through the actual worker path. These are future acceptance
requirements, not outcomes proved by the current routine command suite.


The sealed-source combined native suite is terminal: **137 passed / one
non-native deselected**, zero failures/errors/skips, 721.30 seconds
(`/tmp/core-routine-mandate-sealed-native.xml`). It includes the immutable
human-card provenance probe and final broker identity/privacy changes. The
CI-faithful selection has reported three failures and is still running; the
candidate is **not qualified for commit**. Full-suite and CI terminal reports
must be inspected, failures diagnosed, and affected gates rerun after any fix.


## CI failure diagnosis and qualification restart

The first sealed CI-faithful run ended **1808 passed / 22 skipped / three
failed**, 863.815 seconds (`/tmp/core-routine-mandate-sealed-ci.xml`). All three
failures were recorded graph-context semantic cases whose authorization work
hit the host capture deadline under concurrent verification load. They passed
unchanged in isolation (**3/3**, 15.55 seconds,
`/tmp/core-routine-graph-timeout-recheck.xml`). A separate whole graph-context
run collected before the budget-fixture edit passed **44 / two native-only
skipped**, 82.24 seconds; its historical filename
`core-routine-graph-semantic-budget.xml` does not make it proof of the later edit.

The source now names the shared host deadline once, retaining the production
value **3.0 seconds**. The SQLite semantic fixture explicitly uses a bounded
30-second test budget; byte, candidate, permission, declaration and revocation
assertions remain unchanged. Deterministic deadline refusal remains separately
tested. Expiry after discovery is now service unavailability, not a narrower-
root input error. The three former semantic failures plus deterministic deadline,
production-default and between-pass expiry checks passed **7/7**
(`/tmp/core-routine-graph-budget-fixed.xml`). This is not a production latency
improvement or SLA claim.

The release CI-faithful rerun uses `/tmp/core-routine-release-ci.xml` and an
external diagnostic reporter that prints failure identities promptly. The two
older full runs remain live and are not passing final-source qualification.
This graph-context follow-through changes the earlier sealed source identity;
refresh the runtime/test manifest, broad qualification and wheel artifact proof
before committing. The earlier 137-case native proof qualifies the routine
boundary, but not an untested claim about the graph-context follow-through.


The earlier full-v2 suite is terminal: **6604 passed / 932 skipped**, 1959.17
seconds (`/tmp/core-routine-mandate-full-v2.xml`). It collected before later
broker/privacy/graph follow-through and is retained as preceding-source evidence.
The final release source manifest is `/tmp/core-routine-release-source.sha256`.
Full backend, CI-faithful, native (including the two native graph-context cases),
staged guards and independent artifact gates now use the
`/tmp/core-routine-release-*` evidence prefix. Runtime changes are frozen while
these final gates run. No commit, push or deployment of the routine candidate
has occurred. The original hardening objective and deferred sprint backlog
remain open; see the [QA handoff](2026-10-16-hardening-qa-handoff.md).


## Final QA pipeline and superseded runs

The prompt reporter exposed one additional native-SQLite contents assertion
that saw a partial graph under simultaneous full-suite load. The shared graph
semantic fixture now has the same explicit 30-second test budget. Both named
deadline probes explicitly restore the production **3.0-second** ceiling; the
independent default-value probe also asserts it. No production ceiling was
increased and no byte/candidate/privacy/revocation assertion was removed.

The superseded full/CI processes were gracefully interrupted after this actual
failure and test-fixture correction: `core-routine-mandate-sealed-full`,
`core-routine-release-ci`, and `core-routine-release-full`. Their exit code 2
is an intentional interruption, not a green gate. They were not restarted due
to an observation timeout. The preceding full-v2 and 137-case native terminal
results remain valid only for their recorded source. The later 139-case native
selection and release artifact gate were left running; the artifact gate passed
with SHA256 `7184e7bf36aff07e96549b3ab2676964b8f2f69abf3a39586ed99f4ab44c940c`.

The final pipeline is `/tmp/qualify-core-routine-release.py`, live runner log
`/tmp/core-routine-qa-final-runner.log`. It verifies the 1688-path source manifest
`/tmp/core-routine-qa-final-source.sha256` before and after every stage and runs
whole graph tests, CI-faithful, full backend, native and artifact gates in
sequence. Per-stage evidence uses `core-routine-qa-final-*`; structured results
are `/tmp/core-routine-qa-final-results.json`. It never commits, pushes or
deploys. Final terminal results and fresh staged guards remain required before
committing/pushing this cut. No further feature work is included in this cut.


The expanded release native component run is terminal: **139 passed / 47
non-native deselected**, zero failures/errors/skips, 584.17 seconds
(`/tmp/core-routine-release-native.xml`). It includes both native PostgreSQL
graph-context cases with the graph service's unchanged three-second production
ceiling. It collected before the last test-fixture-only consolidation; final
pipeline native evidence will still be recorded separately. Runtime/test source
continues to match the final QA manifest. The sequential CI stage remains live
without reported failures; full/backend and later stages are not yet complete.


Final sequential CI is terminal: **1813 passed / 22 skipped**, zero failures/
errors, 1835 tests (`/tmp/core-routine-qa-final-ci.xml`). XML inspection confirms
all four earlier deadline-sensitive graph cases were selected and passed, not
skipped or deselected. Source hashes still match the final QA manifest. The
pipeline has started its full backend stage on that same frozen source; commit
and push remain pending full/native/artifact completion and final source checks.


Final staged guards and pinned hooks passed again on the frozen source
(`core-routine-qa-final-staged-guards.log`,
`core-routine-qa-final-pinned-hooks.log`). The frozen production lock advisory
check passed without suppressions (`core-routine-qa-dependency-audit.log`), and
the explicit Core-only lane passed (`core-routine-qa-core-only.xml`). Local
Python 3.11 matches the workflow. The full suite remains live with no immediate
failure reports; no passing completion is inferred from progress markers.


Final frozen-source full backend suite passed: **6609 passed / 933 skipped**,
zero failures/errors (7542 tests), 1071.93 seconds
(`/tmp/core-routine-qa-final-full.xml`). The pipeline has started final native
qualification; final artifacts follow. The source manifest passed before and
after the full stage. This is the default backend selection, not every slow,
native or domain-App journey; skipped requirements remain explicit in the
acceptance ledger. Native and artifact terminal results are still required.
Pre-commit artifact epoch is `1792155625`, from baseline `18ea5ef0`; its wheel
checksum identifies that build input, not a checksum independent of ZIP times.
A later committed-head build may normalize timestamps to that new commit.


## Final local QA qualification (supersedes candidate status above)

The sequential pipeline ended successfully on the unchanged final source:
graph 46 passed / two native-only skipped; CI 1813 passed / 22 skipped; full
backend 6609 passed / 933 skipped; native 139 passed / 47 non-native deselected,
zero skipped; all four independent artifact gates passed. All stages have zero
failures/errors. Core-only separately passed 9/9; pinned hooks, staged guards
and the frozen production dependency advisory audit passed. Source manifest
SHA256 is `9ccd5137d68f5b26a72832c61042bfb3a7be2f395100e82bd497ebe32f5bce97`.
Structured terminal results are `/tmp/core-routine-qa-final-results.json`. The
pre-commit wheel SHA256 is
`7184e7bf36aff07e96549b3ab2676964b8f2f69abf3a39586ed99f4ab44c940c`
with SOURCE_DATE_EPOCH `1792155625`. Source and artifact qualification remain
distinct from fresh remote CI and deployed identity. The broader goal and
explicit sprint backlog are still open.
