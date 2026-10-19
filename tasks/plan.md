# October 14–19 QA implementation

User approved all twelve items and the proposed priority order. No version bump until qualification is complete. Base includes the separate aiohttp security commit from PR #159; preserve its provenance. All fixes are generic Core features.

Implement vertical slices, test focused behavior, then run complete frontend/backend and browser qualification. Do not copy named QA prompts into regression tests. Do not mutate live business records for QA.

## Order and acceptance
1. Q12 Stop acknowledges backend termination, fences old completions, exposes retryable errors, admits the next turn only after release.
2. Q06 Approved live tag batches preserve existing tags and resume by durable step receipts; draft revisions remain distinct from publication.
3. Q09 Useful tag errors; Q08 repeated picker opening; Q01 paste/caret editing; Q03 viewport-safe entry menus.
4. Q02 safe status removal across views and existing records; Q04 authorized template deletion available in the correct surface and preserves installed data.
5. Q05 consistent approval changes; Q07 remove Enhance-view action; Q10 compact filter UI; Q11 authorized cross-track global filtering by distinct tag identities.

Checkpoints: focused regressions after each slice, production build after UI groups, full suites and isolated browser smoke before marking all complete. Track evidence and remaining risks in tasks/todo.md. Maintain Core version 0.1.1rc25 throughout this work.

## Invariants preserved

- I-GRAPH-01/02: tags retain canonical model containment; private recovery uses existing scoped execution Objects.
- I-ROLE-01/02 and active-scope rules: cancellation requires thread ownership; tag listing filters authorized containers; column removal rechecks Track configuration authority and refuses shared model edits.
- I-CRUD-01 and I-PROFILE-02: tag changes reuse the canonical tag writer and model command fence; manual column configuration syncs the attached specification, with no arbitrary agent manifest writes or implicit draft publication.
- I-APPROVAL-01/02/05: each live tag creation binds exact approved fields to its durable receipt, retains actor/outcome and returns current authorized readback after retry.
- I-CHAT-01: readable App/Entry summaries and before/after values replace machine field lists. Internal IDs stay in API/receipt data.
- Migration barriers remain enforced before schema edits; removing an enum value checks stored records and configured defaults before the write.
