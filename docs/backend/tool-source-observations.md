# Governed tool source observations

## Purpose and boundary

Apps need to check exact source passages against retrieved content through the
public context, rather than trusting text copied by a model. Core authenticates
the retrieval and its scope; the App owns interpretation, evidence policy,
source selection, review and business outcomes. Authentic retrieval is not
proof that the publisher's claims are true.

## Implementation

1. Resolve each native connector call to a specific authorized mounted row.
   Dispatch that row's registered alias and persist its ID in the broker receipt.
   Personal/shared precedence is resolved once, before execution.
   Row identity alone cannot satisfy the snapshot: the exact tool must also
   appear in that row's retained declaration.
2. Successful native connector reads returning bounded HTTP source text receive
   an opaque `tool_observation` reference (receipt ID and session namespace).
3. `ToolContext.get(ref)` rechecks the original principal, current workspace
   membership, private thread/session containment, the exact mounted connector
   row and current dual invocation policy. A shared App grants no access to
   another person's transcript.
4. Resolve the latest complete encrypted checkpoint for the indicated run. Match
   its tool return to the authoritative receipt, verify the output fingerprint,
   and expose only bounded source fields. Do not return prompts, thinking,
   credentials, other tool results or the surrounding conversation.
5. Return `None` for missing, revoked, ambiguous, expired, corrupted, oversized,
   incomplete or mismatched evidence. Never refetch or reexecute a capability.
   Older unbound receipts are not promoted to verified observations.

## Preserved invariants / self-review

| Invariant | Preservation |
| --- | --- |
| I-GRAPH-01 / I-GRAPH-02 | Reuse rooted private sessions and encrypted log Objects; no new disconnected Nodes or parallel transcript store. |
| I-ACCESS-01 / I-RET-01 / I-RET-02 | Original actor and tenant plus current membership, private containment and connector policy are required at every retrieval. |
| I-SUBSTRATE-01 / I-EXT-01 | No App slug, domain fields or imports; source provenance is a generic public context capability. |
| I-APP-DEF-01 / I-SKILL-SCOPE-01 | Context is reached through existing governed App dispatch; no new skill bypass or dispatch surface. |
| I-APPROVAL-01 / I-APPROVAL-02 | Read-only retrieval does not grant write, publish or approval authority. |
| I-CON-01..05 | Exact mounted row, existing registration and dual policy; no arbitrary remote invocation or package/adapter mixing. |
| I-CHAT-01 | Opaque references remain structured machine data, not ordinary founder-facing prose. |

## Qualification requirements

Exercise exact/partial text, modified quotes and receipt fields, stale/deleted
threads, foreign actors/workspaces, missing containment, expired/revoked sessions,
removed/reclassified tools, connector replacement and policy revocation,
encryption failure, absent checkpoints, replay-unavailable results, reserved
payload collisions and output bounds. Confirm no network call, write or unrelated
transcript exposure. Run touched tests, full substrate gate, hooks and artifact
build before committing. Business stays on its reviewed pin until a separately
qualified Core distribution is available; local Core tests do not close native
App or founder journey acceptance gates.

## Local qualification — 2026-10-10

Implementation starts from reviewed Core main `9d13b44e` (rc16). This is a
review candidate, not a published release or a deployed downstream upgrade.

- `make verify`: passed all substrate guards, hooks, pinned formatting, lint
  (existing warnings remain), types, artifact, CI-faithful smoke, frontend and
  default full backend gates.
- Final-code full substrate repeat with four workers and the prescribed
  `not domain_app and not slow` filter: **5,010 passed, 286 skipped**, no errors
  or failures. This includes **51 source-observation cases** exercising real
  scoped encryption with an in-memory Object adapter; authorization dependencies
  are controlled fixtures, not a live provider/browser qualification.
- Frontend: **1,583 passed**; production build passed.
- Final pinned-source Core wheel rebuilt byte-for-byte, SHA-256
  `8850c422d4f4dae1ad30667e1ec468b3b96d37acef6b76d20962e1d451f385ee`;
  independent Core source-projection/receipt import and SDK wheel checks passed.
  Source metadata still carries the base rc16 version; this candidate must not
  be confused with the already published rc16 wheel.
- CI-faithful PostgreSQL spike/contract lane: passed on a separately created
  disposable database, with one restore drill skipped because the backup
  container could not reach its host port. Database and temporary credentials
  were removed. This does not qualify restore, all skipped infrastructure
  tests, or the source resolver against a real external service.
- An additional diagnostic run clearing the default marker filter produced
  30 failures in quarantined legacy `domain_app` scenarios. All 30 belong to
  that excluded group (29 module markers, one explicit test marker). Those
  optional scenarios remain unqualified; no domain packages are added to Core
  to make them pass.

Next gates: review/CI, a qualified Core distribution, package-owned validation
and persistence using this public context, then native default-model/browser
retrieval, approval, save/repeat/readback and revocation cases. Quote selection
and domain interpretation remain App responsibilities. No App acceptance gate
closes on this Core implementation alone.
