# Human principal aliases and operation receipt recovery

Date: 2026-10-15. Status: isolated implementation and focused native qualification complete; full candidate gates pending.
This is part of the existing operation recovery/idempotency hardening scope.

## Evidence

The native probe `test_operation_principal_alias_postgres.py` creates an actual
active Auth identity linked to one graph User, with current workspace membership
and App editor access. The same operation, original input and idempotency key
are invoked first under the Auth ID and then under the graph-profile ID. Its
handler creates a Comment under the same readable Entry on each execution.
Both requests succeed, and the handler creates two committed children.

`/tmp/core-operation-principal-alias-red.xml` records the expected assertion
failure: `calls == 2`, where one exact effect is required. Probe source is in
`/private/tmp/core-operation-parent-projection/backend/tests/contract/`.
This is a source/primitive defect reproduction; no browser exploit or live
runtime mutation is claimed.

`OperationIdentity` currently hashes the supplied principal literally. HTTP,
resident/MCP and worker callers must not obtain a second logical write identity
merely by using the other current identifier for the same authenticated human.
Agent and connector identities remain distinct actual subjects; their issuer is
not an alias for their operation identity.

## Required implementation contract

1. Resolve the actual current human profile and linked Auth identity. Use one
   stable preferred principal (the linked Auth ID where present), retaining both
   verified identifiers as a bounded legacy lookup/claim set. Do not normalize
   arbitrary strings inside the public `OperationIdentity` hash contract.
2. Revalidate that alias set under current identity admission after deterministic receipt locks and before any effect or replay.
   This preserves the established receipt-then-identity lock order. Ordering must cover disable/relink and membership changes; a caller
   cannot supply a different person's ID as an alias. Nonhuman subjects retain
   their original subject ID and exact current delegation contract.
3. Admit all known receipt aliases in one native transaction, in deterministic
   order, before entering a handler. Use the existing receipt namespace and
   insert-if-absent serialization, so an old worker claiming either legacy ID
   cannot race a new preferred-ID worker into a duplicate effect.
4. If exactly one original completed receipt exists, retain its document,
   original input hash, result and public receipt ID. Any newly needed alias
   record may point directly to that original receipt, with complete scope and
   request-hash validation. A pointer never grants result disclosure. Keep
   pointer traversal bounded within the verified two-identifier identity set;
   reject missing, conflicting, cyclic or foreign pointer targets.
5. If neither receipt exists, execute once under the preferred identity, commit
   local effects, outboxes and its completed receipt together, and bind the
   other alias in the same commit. Cancellation or result-admission denial must
   roll back every new alias claim and local effect.
6. A legacy incomplete claim is not permission to execute under the other
   alias. Two independently completed alias receipts are ambiguous evidence of
   prior duplicate effects, even if their JSON happens to match: preserve both
   and report explicit recovery/reconciliation, without executing again.
7. Existing successful receipts are immutable. The service execution result
   needs to retain the selected original receipt identity so public references
   and event/outbox reconciliation never manufacture a new identity for an
   older effect. Old workers encountering a new alias marker must fail closed,
   rather than interpreting it as a new dispatch claim.
8. Apply the same current-human namespace to read-cache access while preserving
   legacy cached results and payload-conflict behavior. Read caches do not
   replace command receipts; conflicting cache rows must not be silently picked
   by query order or turned into a mutating-handler retry.
9. Current result dependency, function, declaration and workspace admission
   remain mandatory after recovery. Missing legacy disclosure fingerprints do
   not acquire a new declaration stamp. The separate opaque applied-outcome
   contract must report the known earlier execution without claiming the current
   domain state, exposing withheld output, or rerunning effects.

## Acceptance matrix

- Fresh Auth-to-graph and graph-to-Auth retries: one handler, one child, one
  original public receipt, second response marked replayed.
- Legacy receipt under either alias: immutable original document/ID and exact
  input identity survive; no handler rerun. Cover missing old disclosure stamps.
- Two completed legacy receipts, incomplete legacy claim, missing/corrupt alias
  target, different original input: explicit recovery/conflict, no new effect.
- Independent PostgreSQL connections, opposing alias order, old single-ID worker
  races, cancellation before/after local effect, and post-commit completion loss.
- Disabled/relinked human identity, stale alias proof, different human with the
  same key, foreign workspace/App/operation, and service issuer versus subject.
- Initial response, read-cache and command-receipt transports preserve output
  redaction and current authorization; HTTP/MCP/resident boundaries do not fork
  the same human's command identity.
- Relevant tests, full source, CI-faithful and clean artifact qualification.

## Candidate boundaries

This probe and plan are isolated from the combined parent/object qualification
candidate `f36bdf2cc801534eda28224c02bf83520d0c1910`, which completed
CI-faithful qualification in `/private/tmp/core-hardening-continuation`.
That candidate's tests cannot close this newly reproduced alias defect. Do not
commit or push a claimed complete recovery fix while it remains unresolved.
No deployment or PR merge is part of this continuation.


## Implemented recovery and current evidence

The candidate resolves the current human Auth/profile pair without changing the
public OperationIdentity hash or actor provenance. It claims both original receipt
IDs in deterministic order in one PostgreSQL transaction. Exactly one existing
successful original is retained unchanged; a direct alias marker points to it.
Incomplete, conflicting, foreign, or independently completed originals stop with
explicit recovery/conflict and no new effect. Older single-ID workers refuse the
alias marker. Current identity is checked after the receipt locks and again at
result admission. Agents/connectors retain their actual subject IDs.

Read-cache lookup now uses a raw, scoped database query with a real limit of two.
The installed jvspatial 0.2.2 Object.find(query, **kwargs) treats limit=1 as a
context.limit field filter rather than a row bound; this previously hid durable
rows after process memory loss. Bare field paths were not the root cause. Corrupt,
duplicate, unavailable, or conflicting durable cache facts fail closed even when
process memory contains an apparently valid result. Returned dictionaries are
copied. These caches remain read optimizations, not command receipts.

Focused evidence (2026-10-15):

- `/tmp/core-operation-alias-relink-native.xml`: 22 native PostgreSQL cases pass,
  including both alias directions, immutable legacy receipts, opposing old/new
  workers on independent connections, cancellation rollback, disabled/relinked
  identities, durable-cache memory loss, corruption, conflicts and outages.
- `/tmp/core-operation-alias-native-regressions.xml`: 69 existing native cases pass
  across service delegation, receipts and nested/parent/object result admission.
- `/tmp/core-operation-alias-neighbors-fixed.xml`: 126 neighboring tests pass.
  The changed-definition fixture now uses an actual rooted user/App so it reaches
  its intended effect-permit seam under the stronger current-identity contract.

This does not yet close the full transport/identity matrix, public metadata
projection, opaque applied-outcome recovery, external effects, or other executor
families. Full source, CI-faithful, artifact and hook gates remain required before
committing/pushing the reconciled candidate. No runtime deployment is included.
