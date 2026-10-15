# Immutable operation effect contract proof

Status: isolated implementation and focused qualification in progress. No
commit, deployment, publication or complete approval-recovery claim.

## Purpose and contract

A past operation receipt identified only by principal/workspace/App/key/input
cannot prove that a particular reviewed declaration ran. The original private
result contains a declaration marker, but opaque recovery must not decode
withheld output to obtain approval identity evidence.

The generic canonical operation receipt now accepts an optional server-supplied
`effect_contract_fingerprint`, an exact lowercase SHA256 digest. App dispatch
supplies its prepared declaration fingerprint. Claim, local graph effect,
contract metadata, saved result and event outbox commit in the same transaction.
The receipt ID and request hash remain unchanged; metadata grants no permission.
This is a framework contract, independent of App slug, field names or business
meaning. It does not prove an arbitrary external side effect succeeded.

Read-only outcome lookup can optionally require an expected contract digest.
It compares independent immutable metadata without decoding private saved output,
resolving a handler or creating/repairing claims and aliases. A different digest
is an intent conflict; missing legacy metadata is insufficient proof. Neither
case becomes journal absence or permission to repeat an effect. The private
observation type and its public opaque receipt reference gain no new fields.

Ordinary legacy receipt replay remains compatible and never backfills a missing
contract digest. Existing result-disclosure authorization still checks the
original saved contract. Legacy past-completion observation remains available
without an expected digest, but cannot automatically consume a proposal whose
reviewed declaration lacks matching original evidence.

Aliases preserve the original root's digest, including absence on legacy roots.
A supplied alias digest inconsistent with its original is corrupt evidence.
An operation key reused against another known stored contract cannot dispatch a
new handler. Invalid expected/supplied metadata is refused before database work.

## Acceptance boundaries

Focused tests cover independent contract observation with intentionally malformed
saved JSON, exact mismatch and legacy refusal, no legacy repair, malformed stored
metadata, handler non-replay and rollback of failed-effect receipt metadata.
Neighboring alias/observation suites and existing App staging/receipt error tests
must retain their behavior. Full backend, CI-faithful, artifact and integrated
approval-consumption qualification remain required before release.

This change supplies necessary evidence for the staged App-operation integration;
it does not implement that reconciliation or close a remaining executor family.
An approval consumer must still validate its immutable original human/Subject,
workspace, intent key, original input, reviewed declaration and current decision
state. Do not replace policy Approval's existing joined effect/decision commit
with a second journal or an outside-transaction execution path.

## Baseline and strict coordinator propagation

The isolated source now includes the exact 15 qualified baseline files from
`edd7817d`, copied after verifying they contained no unique local changes. The
worktree branch still names its earlier `5fd97b8d` parent; the file-level baseline
is authoritative. No environment/runtime overlay was imported.

The private recorded-result coordinator now optionally requires the expected
effect contract at every original-outcome lookup, including final readmission.
Its default remains ordinary own-status observation. An approval consumer can
require independent declaration proof even when current output is withheld;
missing legacy proof or a different original declaration is not replaced by
decoding saved private output. This does not wire staging consumption yet.

Expanded alias/real-dispatch/rollback cases and the coordinator proof matrix
require a fresh sealed-source run on this reconciled baseline. A development
native run overlapped baseline reconciliation and is not final combined-source
qualification. Earlier focused results remain historical; do not use them as
proof of the newly combined source.


## Exact terminal proposal identity

A separate [terminal-intent follow-through](2026-10-15-staged-terminal-intent-proof.md)
records five reproduced native snapshot mismatches and the new private proposal
digest. This complements the effect contract metadata; it does not replace the
original operation receipt or implement staged App-operation consumption. The
combined candidate requires fresh qualification before reconciliation or push.


## Subsequent integration candidate

The isolated follow-through now implements the staged local-operation admission,
atomic canonical receipt/decision commit and handler-free recovery described in
[the integration review](2026-10-15-staged-app-operation-atomicity.md). Original
effect declaration and exact reviewed-proposal fingerprints are independent
journal metadata. Earlier pending statements describe their dated prerequisite
checkpoints. The combined implementation still requires final sealed-source,
CI-faithful and artifact gates; external-effect and broader goal work stays open.
