# Bounded work mandate contract

Status: review schema, atomic pending-review producer and stored-approval binding reader implemented; runtime integration incomplete. No public unattended-work promise follows from schema validation.

`app.schemas.agentive.work_mandate.WorkMandateRevision` is the domain-neutral reviewed snapshot for a bounded outcome. Every nested value is frozen; collection fields become tuples. It includes server-resolved principal/workspace/thread, goal and exact input references, capability versions and targets, optional App/definition pairs, separate external destinations/credential references, model attribution, shared deadlines and finite spend/volume limits, success/stop/result obligations, bounded attempts and reconciliation-before-retry. It contains no credential material, canonical user-message replacement, mutable plan dictionary or client-approved boolean.

`review_digest()` hashes canonical JSON and normalizes equivalent time offsets and monetary representations. Scope and collection order remain part of the review. A changed goal, owner, scope, revision or limit changes the digest. The digest identifies reviewed values; it is not proof of human approval or current access. Future services must revalidate deserialized values before digest verification; unchecked `model_copy(update=...)` is not a validation boundary.

## Integration requirements

1. Resolve scope and capability versions from authenticated current Core state. Draft/schema validation cannot grant access. Store revisions immutably with approval identity/digest and normal resource authorization.
2. Reference the approved revision from existing WorkItem lineage and children. Reuse WorkApproval, lease/fence checks, work recovery and checkpoint services. Never create an App-specific scheduler or a second principal plane.
3. Before every model dispatch/tool/effect, intersect the approved grants with current permissions, capability/definition revision and provider identity. Exact resource references do not replace permission checks, relationship resolution or destination adapter validation.
4. Share durable atomic reservations and reconciled usage across children and retries. Strict USD admission requires a conservative known upper bound before dispatch; absent prices do not mean zero. Schema limits alone do not enforce spending or deadline propagation.
5. Implement fenced pause/resume/cancel on the same lineage, preserving receipts. Pause blocks new dispatch and surfaces in-flight reconciliation. Unknown external outcomes require reconciliation before any repeat. Resume rechecks current authority and remaining limits.
6. Add authenticated generic review/run/control APIs and UI. Qualify restart, lease races, revoked grants, wrong tenant/App/definition, exhaustion, in-flight pause/cancel, duplicate approvals and unknown outcomes through targeted source tests and browser flows.

`work_mandates.load_approved_work_mandate` reads the existing WorkItem and WorkApproval store. It verifies authenticated principal/workspace, snapshot/thread scope, exact plan revision and approval digest, approved status/decision/decider, App definition binding, aware decision/expiry timestamps, the mandate deadline and nonterminal/noncancelled work. A changed snapshot, copied approval from another work item, or wrong scope fails closed. It returns reviewed intent only: it does not dispatch, authorize current permissions, create an approval, reserve spending or establish child inheritance. The reviewed snapshot and approval reference live in `plan.mandate_revision` / `plan.mandate_approval_id`; `plan_revision` binds the exact digest. The internal pending-review producer described below now persists this binding. No public endpoint or executable approval path enables unattended work yet. Admission/control wiring remains required.

Current tests cover immutable snapshots, JSON round trips, required review fields, digest binding/normalization, duplicate scope rejection, finite currency limits, aware deadlines, separate external grants and App definition pairing. Stored-binding unit tests also cover wrong scope before approval lookup, digest tampering, pending/rejected/mismatched approval, expiry and terminal work. Decimal normalization is independent of process precision. They do not qualify an approved run, concurrent budget enforcement or runtime controls.

## Invariants preserved

- I-EXT-01: no App identity or domain branches/imports in Core.
- I-WORK-01/02/03: existing lease authority, effect identity and atomic work units remain the integration target.
- I-WORK-05: a schema or digest cannot substitute for fail-closed approval authority.
- Backend workspace authority and I-RET-01: exact reviewed references never bypass current workspace/resource access checks.
- I-HARNESS-01: existing rooted, tenant-bound resident sessions retain ownership of execution.

This foundation starts the complete durable-work implementation. Executable approval, admission, runtime wiring and browser acceptance remain required; synchronous work and ordinary reviewed writes remain the currently qualified product path.


## Atomic pending review producer

`propose_work_mandate_review` accepts authenticated host principal/workspace/thread and a revalidated immutable revision. It rejects scope mismatch and expired intent before accessing storage. PostgreSQL transactions are required. It creates WorkItem, pending WorkApproval and a `work.mandate_review_proposed` outbox fact in one transaction, using deterministic owner/workspace/mandate/revision identity and the canonical digest. The WorkItem starts at `waiting_for_human`, with no lease or runnable time; no `work.enqueued` dispatch fact is emitted. Equivalent decimal/time representations reconcile the same canonical snapshot. A changed same-revision payload conflicts, requiring an explicit next revision. A retry preserves decided approval state and refuses to reconstruct a missing approval.

This is an internal persistence service, not a public authorized producer: current resource/capability/definition/provider resolution must precede eventual authenticated review exposure. The ordinary approval service rejects queueing a mandate review (`work.mandate_admission_required`); rejection and expiry remain available. A dedicated decision/admission path must verify current authority and reserve shared limits before work can run. No new worker, scheduler or App-specific branch is introduced.

Producer tests use a transaction simulator for commit, duplicate reconciliation, changed revision, semantic normalization, rollback at approval/outbox insertion, missing binding, decided retry, scope rejection, expiry, transactional-store requirement and approval bypass denial. They are source evidence. Separate PostgreSQL contracts verify concurrent duplicate proposals with committed store readback, rollback after WorkApproval or outbox insertion failure, denial of ordinary executable approval, durable rejection and reconciliation of a decided retry. All 64 selected mandate/approval tests pass in disposable PostgreSQL mode (`/tmp/venture-mandate-producer-postgres-final.log`), including those four real-store cases. This does not prove process-crash recovery, shared budget admission or browser acceptance. The full Core `make verify` gate passed with exit 0 (`/tmp/venture-mandate-producer-full-verify.log`), including 1,521 frontend tests, the backend suite and CI reproduction. The default full gate skips PostgreSQL contracts; the separate 64-test PostgreSQL run supplies the store evidence above. Optional external-infrastructure skips remain outside this proof.
