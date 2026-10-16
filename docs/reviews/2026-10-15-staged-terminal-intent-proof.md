# Exact intent in terminal proposal reconciliation

Status: isolated candidate; not committed, pushed, deployed or a completed
App-operation approval integration. The existing canonical operation contract
proof candidate remains preserved in this checkout.

## Reproduced boundary

The native terminal-acknowledgment path previously matched token, principal,
kind and terminal state but accepted a snapshot carrying another workspace,
payload, machine review, conversation or intent key. Five new native cases
failed against that implementation; the original four refusal cases passed.
Evidence: `/tmp/core-staging-terminal-intent-red.xml` (nine cases, five failures).
This is a private closure-reconciliation defect, not evidence that the public
state-only endpoint directly executed a substituted operation.

## Framework change

New terminal records retain a private SHA256 fingerprint of the original token,
principal, workspace, conversation, kind, complete input, creation/expiry,
reviewed summary/diffs, originating interaction and intent key. Mutable decision,
progress and error fields are excluded. Strict canonical JSON refuses unsupported
values rather than coercing them into a misleading proof. New native terminal
records write the digest alongside the decision in the same transaction. The
nontransactional compatibility store also records it on new terminal decisions.

Closure acknowledgment checks this digest when present. Public state readback
continues to omit contents, payload, digest and execution authority. Legacy
markers preserve only their historical state-acknowledgment behavior; no digest
is guessed or backfilled, and this compatibility is insufficient for strict
approved-effect reconciliation. A successful closure acknowledgment must never
be used as an execution permit or a substitute for the canonical effect receipt.

The next App-operation integration still requires the fresh immutable decision,
original human and workspace, exact operation identity/input, matching reviewed
contract, atomic decision/effect completion and committed event outboxes. The
separate operation contract fingerprint identifies what ran; this proposal
fingerprint identifies what was reviewed. Neither substitutes for the other.

## Qualification

Five native red probes establish the prior mismatch behavior. A combined
terminal/command/effect/operation-contract run passed 171 native cases with no
skips, failures or errors (`/tmp/core-staging-terminal-intent-native.xml`).
This source preceded the stricter JSON-shape follow-through: two new pure probes
then reproduced tuple and non-string-key coercion. Those shapes are now refused.
The final source passed 59 pure/default-store cases and all 29 native decision
cases with no skips, failures or errors (`/tmp/core-staging-terminal-final-units.xml`
and `/tmp/core-staging-terminal-final-native.xml`). These sets overlap the earlier
run and must not be added. Pinned hooks passed after formatting.

The native tests used the isolated prepared MCP 1.30 verification environment,
not a fresh sync of this checkout's older lock. Full frozen dependency/source,
CI-faithful and integrated approval-consumption qualification remain required
before this candidate's commit or push. No remaining executor family is closed
by this prerequisite alone. No runtime, Business source or live database changed.


## Subsequent integration candidate

The isolated follow-through now implements the staged local-operation admission,
atomic canonical receipt/decision commit and handler-free recovery described in
[the integration review](2026-10-15-staged-app-operation-atomicity.md). Original
effect declaration and exact reviewed-proposal fingerprints are independent
journal metadata. Earlier pending statements describe their dated prerequisite
checkpoints. The combined implementation still requires final sealed-source,
CI-faithful and artifact gates; external-effect and broader goal work stays open.
