# Next hardening increment: known execution, withheld result

Status: partial implementation on `4d2e0bf1` in the hardening worktree.
The read-only storage primitive and native tests are implemented; current
own-history admission and transport integration remain unimplemented. R2 and
the full hardening goal remain incomplete. No push, deployment or public
recovery behavior is claimed for this increment.

## Observed current seams

- invoke_app_operation performs App/function/input/decision admission before
  resolving the current operation principal or durable receipt. Preflight
  revocation therefore prevents acknowledging an earlier committed execution.
- authorize_replayed_result and OperationResultContext correctly withhold
  outputs whose current function, contract, workspace or dependencies fail.
  A receipt does not grant permission to execute, disclose or hydrate them.
- OperationExecutionReceipt is the existing primary journal. Its context binds
  workspace, App, operation, actual principal, idempotency key and request hash.
  It records status/result_json. receipt_reference currently exposes only the
  deterministic receipt ID, status and replayed flag; key/hash remain private.
- The ordinary_authority_call decorator only binds the governance mode. It is
  NOT evidence of selected-workspace admission. A new recovery path must repeat
  authenticated identity and validated request/workspace binding explicitly.

## Required contract

1. Treat completed execution, readable result and current domain state as three
   independent facts. Minimal wording: recorded earlier execution completed;
   result withheld; current records not verified. Never imply present state.
2. Reuse the journal and the original successful identity. Do not introduce a
   replacement journal, re-stamp a legacy declaration, rewrite a successful row,
   manufacture hydrated object_refs, or invoke any handler to recover metadata.
3. A read-only known-outcome lookup may examine at most the current verified
   principal and one linked alias. Validate exact entity, deterministic row ID,
   all scope/principal/key fields and original canonical request hash. Direct
   alias pointers must remain inside that set and resolve to exactly one original.
   No claim insert, alias backfill, outbox write or lease mutation on this path.
4. Keep current authentication and selected-workspace access mandatory, including
   validated transport scope rather than a caller-supplied string. Revoked or
   disabled identity, stale/relinked alias proof, foreign workspace, invalid
   service/connector credential, expired grant and unavailable admission fail
   closed. App/Track/function/output revocation can withhold domain data without
   erasing an otherwise authorized caller's own execution status.
5. Human, agent and connector ownership must be explicit. An issuer is never an
   alias for its delegate. Define/reuse a minimal current own-execution-history
   observation contract for nonhuman principals; do not silently reuse revoked
   execute authority, grant metadata to an issuer, or claim a human-only fix
   closes the full requirement. Workspace membership loss remains a denied
   workspace request, not a new personal-history endpoint in this increment.
6. Check known receipts independently of the current input schema/function
   registration where necessary. An exact original request may no longer validate
   under today's definition, or its App may have been removed. A positive journal
   proof can acknowledge history without loading handler code or current objects.
   Absence is never authority: ordinary current execution admission still applies
   and the normal atomic claim remains the only path to new effects.
7. Conflicting input, incomplete claims, multiple completed originals, corrupt
   identity/scope fields, foreign/cyclic/missing alias pointers and storage outages
   remain explicit conflict/recovery/unavailable states. Do not turn ambiguity into
   succeeded or retry. A malformed result JSON is not license to replay; keep the
   precise status claim limited to what the supported journal contract proves.
8. Result release stays current and bounded. Returning private output remains
   contingent on the existing declaration/dependency gates; missing old disclosure
   stamps do not acquire new ones. If those gates withhold data, re-admit the
   minimal identity/workspace/own-history contract before emitting opaque status.
   Do not catch arbitrary errors or turn identity outages into metadata success.
9. Preserve stable receipt IDs and existing conflict/error codes. Add an explicit
   typed output-disposition field with an allowlist, e.g. available/withheld, and
   a separate current-state-verification flag. No titles, raw object IDs, original
   input/key/hash, stored result, private policy reasons or hidden resource links
   belong in the opaque response. No timing-derived success assertion.
10. Carry the distinction through HTTP, SDK, broker/MCP/resident, staged approval
    reconciliation, durable workers, transcript/reload and UI. Consumers must not
    interpret withheld output as an absent execution, request a second approval,
    rerun the mutation, fabricate a business result or permanently poison a thread.

## Acceptance matrix (native where transactional ordering matters)

- Fresh effect commits once, then current Entry/Track/App/function read or action
  authority changes: same exact request yields original opaque receipt, no handler
  call and no new claim, child, outbox event or approval token.
- Initial committed outcome followed by completion/transport loss, worker-memory
  loss and a second authenticated request; repeat the status without re-execution.
- Current definition/handler/input schema removed or changed; old disclosure stamp
  absent; output dependency reparented/hidden; unknown/unsupported metadata; oversized
  or malformed stored output. Separate status evidence from current-state readback.
- Both current human alias directions and legacy originals preserve original IDs.
  Conflicting/multiple/incomplete/foreign alias rows remain recovery, never success.
- Current workspace mismatch/revocation, disabled/relinked identity, different human
  sharing a key, service issuer-versus-subject, expired/revoked/nonmatching delegation
  and policy changes during release. No metadata disclosure across those boundaries.
- Independent PostgreSQL connections: writer wins before observation and observer
  sees committed history; writer revocation wins before release and data stays hidden.
  Cancellation and rollback leave no successful journal proof to acknowledge.
- Negative lookup does not create a claim, hydrate objects, load an App handler,
  emit events or invoke a provider. Absent denied requests stay denied.
- Existing stable conflict/recovery/unavailable boundary cases retained, plus
  transport/readback tests demonstrating that an opaque success is not domain success.

## Open design work before implementation

Confirm the exact own-history authorization seam for every subject/transport;
choose the typed outcome schema and adapter changes; distinguish explicit result
withholding from unavailable identity admission; specify legacy journal status
qualification. Do not weaken current enforcement to make happy-path tests pass.

## Implemented storage boundary

`execution_receipts.lookup_operation_outcome` reads the existing canonical
journal through one native transaction `find_many` call for at most two exact,
deterministic IDs. The original successful identity is retained. It validates
entity, full namespace, exact request hash and direct alias marker consistency.
Multiple originals, incomplete rows and corruption raise the existing recovery
exception; payload mismatch retains the existing conflict exception. Storage
outages propagate. A missing record returns absence, never execution authority.

No claim, alias repair/backfill, outbox or graph write is performed by this path.
It does not parse saved output, hydrate any domain object, load an App handler or
call a provider. `OperationOutcomeObservation` is private, immutable and suppresses
identity in its repr. Only its independently generated `reference()` dictionary
is suitable for release after current own-history admission; serializing the
private dataclass would expose its internal idempotency identity and is forbidden.
A recorded success does not assert readable output or present domain state.

### Evidence

- Deferred probe reproduced the missing primitive in the actual backend/native
  fixture environment: `/tmp/core-outcome-observation-native-red-qualified.xml`.
  The earlier root-cwd invocation was a collection failure and is not red proof.
- New native storage matrix: 29 passed, zero skipped/failed/errors
  (`/tmp/core-outcome-observation-native-green-qualified.xml`). This includes
  independent connections observing an in-flight command, commit and cancellation
  rollback; no uncommitted outcome is returned as successful.
- Initial fixture follow-through corrected a false substring match against the
  test idempotency key, and warmed the object collection before the writer began.
  The latter separates journal visibility from uncommitted first-use schema DDL.
  The failing probe is retained at `/tmp/core-outcome-observation-native-green.xml`.
- Combined receipt, alias and independent-concurrency PostgreSQL selection:
  59 passed, zero skips/failures/errors
  (`/tmp/core-outcome-observation-neighbor-native.xml`). This includes the new
  29 cases; the counts overlap and must not be added.
- Input bounds/immutable reference and existing receipt-error unit selection:
  30 passed, zero skips/failures/errors
  (`/tmp/core-outcome-observation-units.xml`).
- Pinned formatting, imports, lint, convention and backend mypy gates passed on
  all three Python files (`/tmp/core-outcome-observation-three-hooks.log`).
  Final whole-source default backend: 6,482 passed, 730 skipped, no failures/errors
  across 7,212 cases in 1,481.483 seconds
  (`/tmp/core-outcome-observation-full-current.xml`). The new 29 native cases are
  skipped in this default lane and covered by the separate zero-skip native run.
- CI-faithful smoke: 1,776 passed, 22 skipped, no failures/errors
  (`/tmp/core-outcome-observation-ci-current.xml`); TESTING=1, DEBUG=false,
  no backend .env, xdist and the CI marker selection.
- All four frozen candidate files matched their qualification manifest before
  this final evidence-only report update. Staged substrate/module guards passed
  (`/tmp/core-outcome-observation-staged-guards.log`). Artifact, public transport
  integration and release qualification remain separate and incomplete.

## Next implementation boundary

Build a current own-execution-history admission contract for every supported
subject before using this primitive in dispatcher fallback:

1. Human admission must bind the authenticated transport/turn workspace, current
   active Auth/profile link and current membership. A saved preference, raw
   workspace string or `OperationPrincipal.admit` alone is insufficient.
2. Service/agent/connector admission must validate the actual bound caller,
   current subject/credential/issuer provenance, workspace and grant expiry or
   revocation. Never substitute the issuer's principal or alias for its delegate.
   An explicit own-history action is a possible policy seam, but it must be
   defined in canonical action/delegation contracts and qualified before use;
   the existing nonhuman `OperationPrincipal.admit` yields without validation.
3. Repeat admission before opaque status release, independently of App/function
   result disclosure. Current function denial can withhold output; identity,
   workspace, grant and admission outages must never become metadata success.
4. Integrate the original exact request/key before current schema/handler lookup,
   preserving existing fresh-execution admission on absence. Carry explicit
   output disposition and current-state-unverified semantics through every
   consumer named in the acceptance matrix above.

This ordered follow-through preserves the full original requirement; the storage
primitive alone does not close any transport, authorization or recovery row.
