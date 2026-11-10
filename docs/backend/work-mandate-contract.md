# Bounded work mandate contract

A WorkMandateRevision describes an immutable reviewed outcome with server-resolved principal, workspace, thread, input references, capability versions/targets, external destinations, credential references, model attribution, deadlines, limits, success/stop obligations, attempts, and reconciliation requirements.

**Current boundary:** review schema, pending-review production, stored approval binding, shared accounting, and physical-model admission foundations exist. Exact Core Entry-read execution has a dedicated opt-in admission path. General capability, write, and external-effect execution remain unavailable.

## Review identity

Canonical JSON produces a digest over validated values, preserving meaningful scope and collection order while normalizing equivalent time and monetary representations. A changed goal, owner, revision, or limit changes the digest. A digest identifies reviewed content; it does not prove approval or current access. Revalidate deserialized values before verifying identity.

No credential material, client-approved boolean, mutable model plan, or replacement user message becomes authority. Stored approval and immutable lineage must match the exact reviewed revision. Mandate readers verify both the work record and approval from durable storage. A review still parked for human action requires a separate admission step, even when the stored decision is approved.

An authenticated owner can request a pending review with `POST /api/chat/threads/{thread_id}/mandate-reviews` on an active native conversation. The host binds the principal, workspace, and thread under PostgreSQL locks. A retry of the same mandate ID, revision, and terms returns the existing review. Changed terms require a new revision. The terms express requested grants. Their current availability and authority must be resolved at admission.

When Entry-read execution is enabled, the chat proposal form accepts a goal, up to 12 Entry links or IDs, a deadline, and a USD spend ceiling. Its specialized endpoint derives the current native route and the exact Core read grant, pins accessible source revisions, and saves a pending review with one model request and zero write or external-effect authority.

An authenticated owner can inspect an existing general review at `GET /api/chat/threads/{thread_id}/work-items/{work_item_id}/mandate-review`. The route verifies current workspace access and exact thread ownership, then checks the stored revision and its linked approval. It returns the decision state and reviewed terms with internal credential references omitted. The returned review digest binds the full stored revision and should be treated as opaque by clients.

For exact Core Entry reads, the review includes `entry_read_preflight`. It checks the current capability declaration and version, rooted workspace placement, and the owner's current Entry read policy. Core pins the current Entry record revisions into new reviews and binds those revisions into the digest. An edit makes the pinned review ineligible. It reports `unsupported` for other grant shapes. This check covers Entry read eligibility only. Execution admission still has to verify model, budget, and effect authority.

The review also reports `model_route_preflight` for the native conversation. Core resolves the current heavy route, requires its provider, model, credential source, and credential reference to match a reviewed route, and checks whether host bounds and pricing hooks are configured. A platform route also passes the host quota gate. This preflight does not reserve budget or quote a physical request.

The owner can reject the exact review with `POST /api/chat/threads/{thread_id}/work-items/{work_item_id}/mandate-review/reject` and the opaque `review_digest`. PostgreSQL records the rejection, terminal work state and transition event atomically. Repeating the same rejection returns the durable decision.

The owner can approve the exact revision with `POST /api/chat/threads/{thread_id}/work-items/{work_item_id}/mandate-review/approve` and the opaque `review_digest`. Approval currently supports exact Core Entry reads. The decision rechecks the current grants, reviewed route and deadline under the scoped transaction and stores one decision fact. The approved review stays parked for separate execution admission.

## Admission and execution

A runnable implementation must intersect reviewed grants with current resource, capability, definition, route, and provider authority before every effect. Children remain bound to the approved lineage and shared finite limits. Reuse WorkItems, WorkApproval, leases, fences, checkpoint/recovery, and authenticated controls rather than another scheduler or principal plane.

The dedicated Entry-read worker handles an admitted root and reads only the pinned Entry titles and bodies. It enforces reviewed call counts and a bounded evidence payload, rechecks evidence before model dispatch and before publication, and commits its private chat result with leased completion. Derived result text is omitted from ordinary model-history fallback. The authenticated owner requests queue admission separately using `POST /api/chat/threads/{thread_id}/work-items/{work_item_id}/mandate-review/run` with the same digest. `INTEGRAL_MANDATE_ENTRY_EXECUTION_ENABLED` defaults to false and gates new admissions. When enabled, Core rechecks the approved decision, pinned evidence, current model route, and deadline before an atomic queue transition. This path permits one worker attempt.

Budget reservations bind deterministic logical effect identity, reviewed external destination, and shared aggregate/destination counters. Root-first lineage locking and current lease validation prevent sibling or canceled work from independently expanding the budget. Legacy unattributed records require reconciliation rather than guessed attribution.

A fenced dispatch intent precedes a physical call. Unknown outcome retains its hold; definitive persisted provider cost is required for cost settlement. An untracked legacy reservation cannot be treated as proof that no dispatch occurred. Intent is coordination evidence, not proof of a completed network action.

The periodic recovery loop revisits retained model reservations in bounded pages. If an exact, definitive provider receipt arrived after the original settlement attempt, it applies the same idempotent accounting transition. An unavailable or estimated cost leaves the hold open.

Physical model admission uses final mapped SDK inputs, exact route generation/request identity, trusted bounded pricing, shared holds, stored receipts, and a single-use readiness recheck. Quote and bounds expiry are checked immediately before entering the SDK. A restarted adapter cannot reuse an uncertain physical slot.

## Remaining qualification

Broader tool admission, current-grant integration, child production and controls, provider provenance, interruption/reconciliation, browser recovery, and real outcome acceptance still require qualification. Synthetic accounting tests do not establish a successful unattended business outcome.

Ordinary chat work retains its own lease/permission/route checks and never acquires mandate authority from the presence of a work context. The ordinary capability worker rejects work carrying mandate review markers directly or through a parent before it invokes the capability broker. See I-WORK-05 and I-WORK-07 in [invariants](../INVARIANTS.md).
