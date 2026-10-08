# Bounded work mandate contract

Status: review schema and stored-approval binding reader implemented; runtime integration incomplete. No public unattended-work promise follows from schema validation.

`app.schemas.agentive.work_mandate.WorkMandateRevision` is the domain-neutral reviewed snapshot for a bounded outcome. Every nested value is frozen; collection fields become tuples. It includes server-resolved principal/workspace/thread, goal and exact input references, capability versions and targets, optional App/definition pairs, separate external destinations/credential references, model attribution, shared deadlines and finite spend/volume limits, success/stop/result obligations, bounded attempts and reconciliation-before-retry. It contains no credential material, canonical user-message replacement, mutable plan dictionary or client-approved boolean.

`review_digest()` hashes canonical JSON and normalizes equivalent time offsets and monetary representations. Scope and collection order remain part of the review. A changed goal, owner, scope, revision or limit changes the digest. The digest identifies reviewed values; it is not proof of human approval or current access. Future services must revalidate deserialized values before digest verification; unchecked `model_copy(update=...)` is not a validation boundary.

## Integration requirements

1. Resolve scope and capability versions from authenticated current Core state. Draft/schema validation cannot grant access. Store revisions immutably with approval identity/digest and normal resource authorization.
2. Reference the approved revision from existing WorkItem lineage and children. Reuse WorkApproval, lease/fence checks, work recovery and checkpoint services. Never create an App-specific scheduler or a second principal plane.
3. Before every model dispatch/tool/effect, intersect the approved grants with current permissions, capability/definition revision and provider identity. Exact resource references do not replace permission checks, relationship resolution or destination adapter validation.
4. Share durable atomic reservations and reconciled usage across children and retries. Strict USD admission requires a conservative known upper bound before dispatch; absent prices do not mean zero. Schema limits alone do not enforce spending or deadline propagation.
5. Implement fenced pause/resume/cancel on the same lineage, preserving receipts. Pause blocks new dispatch and surfaces in-flight reconciliation. Unknown external outcomes require reconciliation before any repeat. Resume rechecks current authority and remaining limits.
6. Add authenticated generic review/run/control APIs and UI. Qualify restart, lease races, revoked grants, wrong tenant/App/definition, exhaustion, in-flight pause/cancel, duplicate approvals and unknown outcomes through targeted source tests and browser flows.

`work_mandates.load_approved_work_mandate` reads the existing WorkItem and WorkApproval store. It verifies authenticated principal/workspace, snapshot/thread scope, exact plan revision and approval digest, approved status/decision/decider, App definition binding, aware decision/expiry timestamps, the mandate deadline and nonterminal/noncancelled work. A changed snapshot, copied approval from another work item, or wrong scope fails closed. It returns reviewed intent only: it does not dispatch, authorize current permissions, create an approval, reserve spending or establish child inheritance. The reviewed snapshot and approval reference live in `plan.mandate_revision` / `plan.mandate_approval_id`; `plan_revision` binds the exact digest. No producer or public endpoint enables this path yet. Atomic producer/control wiring remains required.

Current tests cover immutable snapshots, JSON round trips, required review fields, digest binding/normalization, duplicate scope rejection, finite currency limits, aware deadlines, separate external grants and App definition pairing. Stored-binding unit tests also cover wrong scope before approval lookup, digest tampering, pending/rejected/mismatched approval, expiry and terminal work. Decimal normalization is independent of process precision. They do not qualify an approved run, concurrent budget enforcement or runtime controls.

## Invariants preserved

- I-EXT-01: no App identity or domain branches/imports in Core.
- I-WORK-01/02/03: existing lease authority, effect identity and atomic work units remain the integration target.
- I-WORK-05: a schema or digest cannot substitute for fail-closed approval authority.
- Backend workspace authority and I-RET-01: exact reviewed references never bypass current workspace/resource access checks.
- I-HARNESS-01: existing rooted, tenant-bound resident sessions retain ownership of execution.

This schema starts the complete durable-work implementation. Approval persistence, admission, runtime wiring and browser acceptance remain required; synchronous work and ordinary reviewed writes remain the currently qualified product path.
