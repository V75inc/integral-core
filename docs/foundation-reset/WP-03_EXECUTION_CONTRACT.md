# WP-03 execution contract

This is the authoritative cutover contract for commands, approvals and durable
work. A proposal is not evidence that an effect occurred. A chat response,
staging card, broker step or HTTP response is likewise not an independent
source of execution truth.

## Authority and identity

Every mutating declared App operation uses an `OperationIdentity` made from
workspace, App instance, operation key, principal and client idempotency key.
The declared operation kind determines whether the path is a command: an
`execute` or `propose` operation is durable even when it omits `policy_action`
and is normalized to the command policy default. A pure handler must declare
`kind: read`; a policy default must never downgrade a command into an
in-memory operation.
Its canonical request hash binds the input. The dispatcher claims one
Postgres receipt, writes local graph effects and an operation-event outbox in
the same transaction, then stores the completed result. PostgreSQL capability
is mandatory for this path; unavailable transaction support returns a
fail-closed error.

The capability broker is an adapter around that authority. Its `RunStep` is a
trace receipt. For a completed App command it re-enters the dispatcher with
the same idempotency key, which reads the operation receipt instead of running
the handler again. HTTP, MCP, resident and custom-view calls therefore share
one effect identity and result.

Read operations can use their bounded replay cache because they produce no
local effect. They are never evidence for a completed mutation.

## Work plans and continuation

`WorkItem` is the durable authority for asynchronous continuation. It stores:

- the immutable input fingerprint plus plan revision and plan;
- ordered prerequisite work IDs;
- a pre-commit draft and remaining obligations;
- lease token/fence, deadline, cancellation state and retries; and
- non-secret receipt references for the broker step and operation receipt.

A dependency must be `succeeded` before its dependent work can claim a lease.
A missing or terminally failed prerequisite fails the dependent work with
`work.dependency_unmet` and records the blockers as remaining obligations.
Continuation reads these records and receipt references. It must not infer
completion from a prompt such as “continue”.

Cancellation prevents future effects but does not erase committed receipts or
effects. Responses must show any committed receipt reference and remaining
obligation. Approval decisions are one-shot durable `WorkApproval` decisions;
repeating a resolved decision has no additional effect.

## Revision, access and external effects

The capability broker denies execution when its capability snapshot is revoked
or changed. A changed plan revision is a new logical request: reusing the old
work idempotency key fails with `work.idempotency_conflict`. Lease token and
fence are checked before an effect and again before completion. Deadline and
cancellation gates fail closed at the same boundary.

Local database effects are authoritative once their operation receipt commits.
For an external provider, a timeout or response loss is `unknown_outcome`.
The operation remains unreconciled until the provider correlation ID resolves
it; retrying must reuse that exact correlation and idempotency identity rather
than issuing a fresh side effect.

## Pending legacy migration

Only pending legacy approvals and work records are candidates. Completed or
terminal historical records remain audit evidence and are never replayed.

Translate a pending record only when all of these are present and verifiable:

1. principal, workspace and target App/resource identity;
2. exact definition or policy revision used when it was proposed;
3. exact effect identity or a provider correlation ID, and canonical request
   payload sufficient to calculate its request hash; and
4. an unexpired authorization that still passes current access and policy.

Create the new `WorkItem` and, where required, `WorkApproval` with those
identities. Preserve a `causation_id` pointing to the source record. If any
condition is missing, expire the source with the explanation
`legacy_identity_unverifiable`, retain it for audit, and require a new
proposal. Never convert a legacy pending card into runnable work by guesswork,
and never silently replay it.

## Qualification evidence

The Postgres receipt contracts prove atomic graph effect/receipt/outbox,
injected rollback, concurrent duplicate invocation and request-hash conflict.
The work contracts prove atomic work/outbox transitions, lease fencing,
one-shot approvals, cancellation, expiry and recovery. Browser qualification
still requires an authenticated deployed build and checks the user-visible
receipt, partial outcome and reload behavior.
