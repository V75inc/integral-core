# Hardening follow-up

This is the maintained backlog carried forward from the retired review timelines. Existing implementations and passing focused tests do not close these wider acceptance requirements.

## Approval ownership and outcomes

- Complete the remaining effectful command families and per-child mixed-batch identities. Preserve original principal, workspace, reviewed intent, current approval and worker lease. Refuse retired design-card effects.
- Qualify cancellation, expiry, revocation, independent workers and process restart without duplicate effects. Keep local App-operation and policy-Approval transaction ownership explicit.
- Complete external dispatch intent/readback and unknown-outcome reconciliation. Never blindly replay uncertain effects; keep external I/O outside held authority transactions.

## Disclosure and graph history

- Complete the final-source multi-connection stream/revocation-ordering matrix, including sandboxed extension replies and frame replacement. Already-admitted transport bytes cannot be recalled.
- Reconcile request-selected workspace, canonical containment, live resource admission and Personal scope across HTTP, native and history consumers.
- Qualify populated history query plans in both directions, including tied/deleted continuation boundaries and pinned-adapter index alignment. Existing probes establish no production latency SLA.

## Durable scheduling and shared operation

- Complete atomic due-slot enqueue/advancement, durable completion/run-budget accounting and independent-process restart/fault probes.
- Keep automatic scheduled writes restricted to typed target grants, exact broker provenance, current authority and supported effect families. Unsupported and human proposals retain review requirements.
- Qualify admission limits across replicas; per-process controls are not shared-service guarantees.

## Privacy, migration and maintainability

- Qualify declared-reference metadata, deleted-history audiences, attachment bytes, session invalidation, restore/migration and ordinary-member journeys.
- Reconcile legacy relation identities without losing data. Continue canonical command consolidation, classified errors and scoped directory policy.
- Complete wider file-picker, provider, connector, native-installer and production-deployment matrices; individual browser checks do not establish these outcomes.

Use the [qualification guide](../ops/QUALIFICATION.md) for gates and [release record](../ops/RELEASE_QUALIFICATION.md) for evidence from the actual candidate. This backlog is not a claim that a particular release implements or qualifies every item.
