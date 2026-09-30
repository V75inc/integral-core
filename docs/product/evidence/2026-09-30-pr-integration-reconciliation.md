# Core PR integration reconciliation — 2026-09-30

The integration candidate for PR #96 incorporates the useful Core changes from
PRs #70–#95, excluding PR #91's commercial integration. PR heads were compared by
file content and patch behavior because rebased wave branches are not literal
ancestors of the assembled candidate. The current acceptance ledger remains the
release authority; merging source is not an independent C6 human signoff.

Reviewed branch identities are retained in [the source manifest](pr-integration-sources.json).

## Reconciled gaps

- PR #83: focused Entry/Track/View names and IDs, review-only workflow, native
  tool protocol configuration, unambiguous `action_input` envelope adaptation,
  server-computed profile diff gate, visible diff before resume, and regressions.
- PR #89: chart modal regions use `Modal.Body`, alongside the dashboard modal
  padding and denial feedback already integrated.
- PR #79: resumed durable-batch assertions retained alongside the primary-key
  lookup and fail-closed cancellation implementation.
- PR #94: loaded CI image identity reporting; the integration's extra exclusion
  of `.qualification-evidence` from Docker contexts is preserved.
- PR #95: SOURCE_DATE_EPOCH normalization, repeat Core wheel byte comparison,
  clean install/SDK/external-App build setup, and publishing workflow timestamps.
- Current dependency audits: urllib3 upgraded from 2.7.0 to 2.8.0; axios lock
  refreshed to 1.20.0. Audit enforcement remains enabled.

The wave package evidence and previously missing historical browser/requalification
notes are retained with their own source identities. Those records do not qualify
a later integration SHA automatically. Current finish-status and acceptance-ledger
records take precedence over historical notes.

## Overlap decisions

| PRs | Resolution |
| --- | --- |
|70,71|Retain scale pushdown and batched ACL logic together with permission cache refill fencing.|
|72,73|Retain schema migration patches, revision-driven field edits, and newer skill routing.|
|74,75|Retain atomic moves plus complete tag mapping and Track restructuring; do not revert to the earlier move-only contract.|
|76,77,78,81,92|Retain the combined dashboard aggregate/suggestion/widget/drill-through/declared-query path and newer query total-estimate assertions.|
|79,80,82|Retain durable batch recovery, effective skills/approvals and current routing ownership.|
|83,89|Apply missing focused-review/diff-gate and chart-padding patches; preserve newer query and frame fixes.|
|84,85,86,87|Retain qualification evidence with its original SHA and current integration status separately.|
|88,90|Ollama implementation and lazy Object restore correction already match the candidate.|
|93|Original PyJWT/frontend upgrades retained, with newly fixable advisories patched.|
|94,95|Apply missing build/CI changes; preserve integration-specific exclusions.|
|91|Excluded pending the separate Core/Business commercial contract decision.|

## Invariant review

I-SUBSTRATE-01 and I-EXT-01: no domain implementation or commercial billing
module is introduced. App dispatch remains through the shared broker and
published contracts. I-GRAPH-01/02: no new persistence primitive or unattached
Node is introduced. Principal/workspace checks remain server-bound. Approval
of a profile edit authorizes only an unpublished draft; its review and publish
remain separate. Existing transaction and durable receipt paths are preserved.

## Qualification

The reconciled source passed `make verify` (1277 frontend tests plus the full
backend lane), `make test-postgres` (12 explicit skips),
`make verify-independent-artifacts`, and both dependency audits.
Local gate logs are retained in `.qualification-evidence/reconciled-*.log`.
Final source, CI, registry and browser results are recorded in the current
acceptance ledger after the candidate is frozen. Historical `eee9b51` image and
browser receipts remain historical and are not relabeled as this source.
