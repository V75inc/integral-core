# Staged local App-operation atomicity and recovery

Status: qualified local increment prepared for PR #136. Full backend,
CI-faithful smoke, combined native, supplementary expiry/revocation and all four
independent artifact checks passed. Remote CI is a separate gate. No merge,
deployment or full R1/R2 closure claim.

## Ownership and evidence

The canonical App operation remains the only effect journal. A private task-bound
producer scope admits the exact current reviewed proposal inside that operation's
existing transaction. Ordinary identity, workspace, function, declaration,
resolved-input and resource authority still apply. Proposal admission never
replaces them or impersonates another Subject.

The journal independently records two SHA256 fingerprints: the original effect
declaration and the original complete reviewed proposal. The latter includes the
proposal token/actor/workspace/conversation, original input and review. Matching
only a declaration, key and input would let a different proposal claim an older
execution. Neither fingerprint is a public result or execution permission.
Legacy absence is insufficient strict proof; it is not guessed, backfilled or
recovered by decoding private output. Existing principal aliases retain their
original root's metadata without creating another successful root.

The operation's graph changes, successful receipt, event outbox and consumed
proposal/terminal marker commit together. One shared private terminal writer
retains exact intent proof and refuses conflicting terminal facts. Notifications,
transcript/navigation closure and output disclosure follow the commit. Their
failure cannot undo the completed decision or authorize another handler call.
Normal policy Approval retains its existing transaction owner and original
Subject; it does not install this staging producer scope or gain another journal.

## Recovery and refusal

Recovery observes the canonical original receipt under current own-history
admission, matches both independent fingerprints, and checks the exact current
proposal or retained terminal proof. It can consume a known original completion
without loading the current handler. Final output goes through fresh current
function/dependency disclosure; unavailable output produces the existing opaque
withheld DTO. A receipt does not verify current business state.

This path refuses ambient effect transactions before dispatch. Its producer
binding is one-use and belongs to the original asyncio task; it is removed before
nested ordinary operations or post-commit event work can inherit its metadata.
An active proposal contradicting an existing terminal marker requires explicit
reconciliation. Revoked decisions, different reviews, legacy/incomplete proof
and unavailable journals never become fresh execution. The process-local cache
claim is not an owner for this integration; the canonical native receipt and
fresh decision serialize independent workers.

## Local versus external effects

This qualifies the canonical local graph command contract. It does not make
arbitrary privileged Python/network/file effects transactional or prove a
provider outcome. The separate full-goal work on explicit external dispatch
intent, effect classification, idempotency/readback and unknown-outcome
reconciliation remains mandatory. Do not generalize the local cancellation test
into permission to replay arbitrary external operations. Unsupported batch
families and the other executor kinds retain their existing boundaries.

## Development evidence and next gates

- Initial actual-approval tests exposed a process-cache recovery dependency and
  an incorrectly placed fault injection. After fixing both, five cases passed.
- A 77-case native operation/decision selection passed before subsequent guards.
- Controlled red probes then proved that an ambient parent transaction could
  dispatch before refusal, and contradictory terminal facts could permit fresh
  dispatch. Both guards now refuse before the handler; a 45-case native selection
  passed after those fixes.
- A 61-case pure/default selection passed for binding task/identity/intent,
  one-use cleanup, strict metadata inputs and proposal fingerprints.
- Two fresh-interpreter tests passed: restart recovery with no handler registry,
  and two independent executing workers producing one actual graph effect. They
  share only the disposable PostgreSQL store and immutable proposal snapshot,
  not a GraphContext, cache or handler. The source path is checked in each child.
- Actual fault tests raise after native decision writes and verify graph,
  receipt and decision rollback. Cancellation leaves no graph/receipt; a new
  admitted local attempt reuses the exact intent and creates one record. Lost
  post-commit closure, function removal, stale revocation, another proposal and
  legacy proof are separate cases.

Evidence is retained in `/tmp/core-staged-operation-*` XML/logs and
`/tmp/core-operation-decision-guards-units.xml`. Counts overlap and cover dated
source snapshots, not one summed suite. The sealed combined qualification below
supersedes those development snapshots for this local increment.

## Sealed candidate qualification

The runtime and original tests are sealed by
`/tmp/core-staged-operation-final-source.json`, based on `c7381289`. The broad
native selection passed 224 tests, with zero failures, errors or skips
(`/tmp/core-staged-operation-combined-native.xml`). It includes actual staging
application, canonical operation receipts, current history admission, principal
aliases, terminal decisions, policy Approval, cancellation, rollback and two
independent executing processes.

Three supplementary expiry/revocation tests passed against the same unchanged
runtime,
with zero failures, errors or skips
(`/tmp/core-staged-operation-expiry-revocation-native.xml`). Their separate source
seal is `/tmp/core-staged-operation-expiry-source.json`. Advancing the admission
clock beyond the original approval deadline prevents a new effect; advancing it
after completion still permits exact receipt observation without handler
resolution. The original full suite collected before these three cases were added;
the supplementary native result is separate evidence, not part of its count.
The revocation race waits for the committed revoked decision before releasing
the paused effect admission and proves that the handler never runs and no
canonical receipt remains. The first probe waited for post-commit linked work
while its artificial barrier held a writer, timing out on cold index DDL; that
synchronization error is retained in the earlier race log and is not a passing
result. The corrected probe tests the durable authority boundary directly.

Pinned hooks and staged guards passed for the original source; the new expiry
test also passed pinned hooks. Full default backend passed: 6,588 passed, 819 skipped, zero failures/errors
(`/tmp/core-staged-operation-full.xml`; 1,177.461 seconds). CI-faithful smoke passed: 1,797 passed, 22 skipped, zero failures/errors
(`/tmp/core-staged-operation-ci.xml`; 482.369 seconds). All four independent
artifact gates passed (`/tmp/core-staged-operation-independent-artifacts.log`):
Core wheel, public dependency clean installation, SDK wheel and external App
loading. The candidate Core wheel rebuilt byte for byte with SHA256
`90ff954cc2498bfb47820631595a8e08b64d6499973b1e78ebeb4c2160d543fe`.
The final source manifest is `/tmp/core-staged-operation-expanded-final-source.json`. These
results do not establish live-provider, browser, external-I/O or complete executor
coverage.

The pinned jvspatial 0.2.2 adapter exposes native begin/commit/rollback on
PostgreSQL. Its SQLite adapter does not expose those methods, so it cannot enter
this native integration. Do not infer SQLite transactional recovery coverage
from the PostgreSQL results or change the existing unsupported-store boundary.


## Cross-transport acknowledgement follow-through

Remote PostgreSQL CI at `0c9fea2c` exposed a compatibility gap: a fresh MCP
approval for an already completed exact operation was rejected rather than
acknowledging the earlier receipt. The new no-effect path explicitly reports
`already_completed`; it does not claim that the later review authorized the
earlier effect. Current proposal identity/state/expiry, independent original
declaration/input proof and current own-history/output admission still apply.
The original receipt and its decision fingerprint remain byte for byte unchanged.
Missing/invalid declaration or malformed decision metadata requires
reconciliation; it cannot become a dispatch permit. Terminal intent supports
recovery of an acknowledged decision without re-entering a handler.

The C6 correction test now uses real current Auth/App authority and prepared
review metadata, rather than a substituted dispatcher with invented identities.
Its private fixture effect is verified directly, without publishing unsupported
Object IDs. Changed mutation declarations retain the explicit idempotency
conflict; read-only replacement contracts retain disclosure denial.

All 26 focused native cases passed (`/tmp/core-ci-repair-all-focused-native.xml`).
The full default backend run passed 6,588 tests with 827 skips and zero
failures/errors (`/tmp/core-ci-repair-full.xml`). Its runtime is unchanged by
the subsequent PostgreSQL-only observation test update.

The initial full native contract run recorded 724 passes, three failures and
three skips (`/tmp/core-ci-repair-pg-contracts.xml`). All three failures were
private dataclass shape assertions after adding the decision fingerprint;
they were reproduced independently and corrected without changing runtime.
The entire updated observation component then passed 30 native tests
(`/tmp/core-ci-repair-observation-shape-green.xml`). The public receipt retains
only its three supported fields, with explicit coverage that private decision
proof and malformed private output never enter it. The three broad-run skips
were the opt-in restore gate, the opt-in slow query-plan gate and a backup drill
whose local `pg_dump` connection was refused. These are unqualified gates,
not passes. The PostgreSQL spike passed one test; the separate CI App-job and
bundle authorization phase passed all 56 tests with no skips/errors/failures
(`/tmp/core-ci-repair-sidecar-app-jobs.xml`).

The earlier qualification above describes the original increment, not this
amended runtime. No new push or deployment yet.

### Pause checkpoint qualification

The final combined current-source PostgreSQL selection passed all 69 tests with
zero skips, failures or errors (`/tmp/core-ci-repair-checkpoint-native.xml`).
It includes the corrected observation component, acknowledgment proof,
C6 correction/expiry, nested contract replacement and staged-operation recovery.
CI-faithful smoke passed 1,797 tests with 22 skips and zero failures/errors
(`/tmp/core-ci-repair-ci.xml`). All four independent artifact gates passed:
Core wheel, public clean install, SDK wheel and external App loading
(`/tmp/core-ci-repair-checkpoint-artifacts.log`). Pinned hooks and all 16 staged
guards passed. No frontend source changed in this checkpoint.

The original broad native run remains recorded above with its three corrected
assertion failures; this is partitioned qualification, not a claim of a freshly
all-green full native run. The backup/slow/restore gates remain outstanding.
Remote CI still describes the previously pushed commit `0c9fea2c`; this repair
is a local checkpoint pending a future push and fresh remote CI. The separate
routine-command candidate remains uncommitted in its own worktree. The overall
hardening goal is incomplete and pauses at this commit; no runtime redeployment
or browser acceptance is claimed.
