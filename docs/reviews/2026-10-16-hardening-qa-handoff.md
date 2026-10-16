# Core hardening QA handoff — 2026-10-16

## QA source identity

PR #136: `codex/core-rc18-jvspatial-022`. The QA cut is the commit containing
this record; the PR handoff records its exact pushed SHA and current remote CI.
Its qualification base is `18ea5ef0ab45ebb0c92a97f226e2ec3cbfb65204`.
That prior source's remote CI run `38904680348` passed backend, frontend,
PostgreSQL and artifact checks; Docker was normally skipped. Do not substitute
that older commit or its CI status for this cut. Final local results below
qualify the source manifest for this cut. Build backend and frontend from the
same recorded source and verify deployed identity before interpreting QA.

The existing local preview was preserved and has not been redeployed by this
handoff. A healthy local URL is not proof that it serves the pushed source.

## QA decision

The prior pushed baseline `18ea5ef0` was available for controlled functional
testing, not completed hardening or production-security sign-off. Scheduled
automatic writes were excluded from that older baseline's QA acceptance: native probes reproduced automatic
approval of unrelated human cards minted mid-run, including after cancellation
or removal of the routine's write grant. The uncommitted candidate replaces
that inference with exact broker provenance and fresh mandate/lease checks.
Do not enable scheduled write grants in a production rollout based only on the
baseline's green CI. Literal reminders and ordinary manually reviewed proposals
are distinct paths; their tests do not qualify automatic scheduled writes.

## Candidate release gates

The current routine candidate includes transactional create/edit/stop/remove
receipts, fresh worker admission and cancellation, serialized progress, frozen
mandates, exact proposal provenance and current-result recovery. Broker receipt
reuse also binds actual operation class and connector identity. Historical
proposal summaries and diffs are withheld from recovered public results.

| Evidence | Result |
| --- | --- |
| Final native routine/Entry/lease/operation/release/graph selection | 139 passed; 47 non-native deselected; zero failures/errors/skips |
| Focused broker, execution-run and provenance selection | 45 passed |
| Pinned format/lint/type hooks and staged substrate guards | Passed on final staged source |
| Frozen production dependency advisory audit | Passed; no known vulnerabilities |
| Independent Core, clean ASGI install, SDK and external App artifact gates | Passed |
| Reproducible candidate wheel SHA256 | `7184e7bf36aff07e96549b3ab2676964b8f2f69abf3a39586ed99f4ab44c940c` |
| Final CI-faithful selection | 1813 passed / 22 skipped; zero failures/errors; all four earlier deadline-sensitive cases passed |
| Final full backend suite | 6609 passed / 933 skipped; zero failures/errors, 1071.93 seconds |
| Commit, push and remote CI | Local cut qualified; exact pushed SHA and fresh remote checks are recorded in the PR handoff |

The three CI failures, any full-suite regressions, final source identity and
remote checks at the eventual pushed candidate are current-cut gates. They are
not deferred merely to make a release appear green. The repository requires
passing applicable full tests and hooks before committing substrate changes.

## Subsequent sprint backlog

The full objective remains in the [acceptance ledger](2026-10-15-hardening-closure-ledger.md).
The following work can be planned separately from this controlled QA cut, with
its limitations retained in release notes:

- **R1/R2:** remaining command families, mixed-batch child identities and
  outbound effect/reconciliation contracts; no blind replay of unknown outcomes.
- **Scheduled fires:** atomic due-slot enqueue/advancement, durable completion
  and run-budget accounting, and independent-process restart/fault probes.
- **R3/R4/R6:** aggregate final-source qualification of stream release,
  request-selected history and execution-scope contracts; admitted transport
  bytes cannot be recalled.
- **R5:** pinned-adapter query/index expression alignment, populated plans in
  both directions and tied/deleted-boundary continuation; no production SLA.
- **Privacy and restore:** declared-reference metadata, deleted-history audience,
  attachment bytes, session invalidation, migration and ordinary-member journeys.
- **Deployment:** shared admission limits across replicas and explicit scoped
  directory policy; per-process limits are not a shared service guarantee.
- **Legacy data and maintainability:** reviewed relation-identity migration,
  remaining shared command consolidation, classified errors and reconciled docs.

This split does not mark the original hardening goal complete. Merge to main,
production release and redeployment require their own authorized handoff.

The three initial CI failures were deadline-sensitive graph semantic tests,
not routine command failures. They passed unchanged in isolation. Production's
three-second ceiling is preserved; semantic fixtures now have a separate test
budget and deterministic timeout assertions remain. Between-pass deadline
expiry is correctly classified as unavailable. Final broad CI/full and artifact
qualification are still required after this follow-through.

The final qualification now runs graph semantics, CI, full backend, native and
artifacts sequentially under a source manifest. Superseded parallel runs were
gracefully interrupted after an additional deadline-sensitive SQLite contents
assertion. All graph semantic cases use an explicit test budget; named timeout
tests restore the production limit. Final results are not yet release sign-off.

## CI performance follow-up

The final default full suite took 1071.93 seconds on this local host. Recorded
JUnit durations include two approved-relation build/readback cases at about
161 seconds each and reference extension/readback cases around 123–132
seconds. These are end-to-end fixture/test durations, not production request
latency. At the tail only one worker remained active under loadfile scheduling;
additional worker count alone does not remove a long serial module. Preserve
the rename/relation/readback assertions while profiling fixture setup, repeated
authorization I/O and the slow module's critical path next sprint. A native OS
stack sample showed reads and synchronization but did not attribute Python
frames; it is not proof of a specific function-level bottleneck.

## Final local qualification

All five sequential stages passed on the unchanged 1688-path runtime/test
manifest: graph (46 passed / two skipped), CI-faithful (1813 / 22), full backend
(6609 / 933), native (139 / zero skipped; 47 non-native deselected), and all four
independent artifact proofs. Core-only separately passed 9/9; pinned hooks,
staged guards and the frozen production advisory audit passed. Every passing
count is selection-specific; overlapping counts must not be added.

The pre-commit reproducible wheel above used SOURCE_DATE_EPOCH `1792155625`
(the qualification-base timestamp). Its digest identifies that exact build; a
new committed-head build may use a different ZIP timestamp. Source manifest
SHA256: `9ccd5137d68f5b26a72832c61042bfb3a7be2f395100e82bd497ebe32f5bce97`.
Pipeline results: `/tmp/core-routine-qa-final-results.json`. No new published
package or deployed runtime is implied by these local source/artifact checks.

The prior-baseline scheduled-write exclusion above applies to `18ea5ef0`. This
cut replaces its unsafe approval scan with the qualified broker/mandate path.
For controlled QA, supported scheduled writes require configured typed target
grants and current authority; human and unsupported proposals still require
review. Durable fire accounting, all effect families and the wider acceptance
ledger remain subsequent-sprint work, not completed release claims.
