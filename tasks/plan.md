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

## October 19 complete follow-up

The original rc25 qualification above is historical. The follow-up integrates
main's rc26 installer fix, reconciles all 26 October 14/19 items from the live
QA document, retires all Enhance surfaces and functions, delays conversation
previews, and uses the actual local frontend/API/PostgreSQL/provider runtime.
See tasks/qa-followup.md for the current acceptance inventory and evidence.
No additional version bump or deployment is implied by the source update.

## Chat recovery and everyday language follow-up

Investigate the reported production failure, repair Home authoring and workflow
starter disclosure, and review host/model failure communication for everyday
users. Preserve current authorization, approval and private-diagnostic boundaries.
Qualify the actual local provider path through saved Home readback and run the
Core gates before pushing the PR update. Evidence and limits:
`docs/qa/2026-10-19-chat-recovery-copy.md`.

- Follow-up steering: replace the hardcoded starter-read disclosure list with
  active skill declarations for reads and proposals; preserve current broker
  and approval authority. Verify generic unrelated skills and history restoration.
- Keep post-approval/rejection replies to one or two short sentences after
  verified readback, without repeating proposals or adding unsolicited offers.

- Local provider acceptance passed for two independent skills in one chat; both
  approval confirmations were one sentence and saved results appeared in the UI.
- Release/commit blocked by a cold-worker PostgreSQL DDL deadlock, reproduced on
  both the candidate and isolated fb0db8be baseline. Preserve the failing contract
  test; follow AGENTS.md stop-before-commit rule for an out-of-scope failure.
