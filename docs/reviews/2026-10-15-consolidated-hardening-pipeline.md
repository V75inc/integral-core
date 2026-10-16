# Consolidated Core hardening and recovered changes

Date: 2026-10-15. Status: source qualified locally; push and deployment acceptance pending.

## Source reconciliation

The remote Core branch baseline is `f508d7ad93562b0839ffc31a78567017eabe3c81`.
The local Business deployment was verified at that commit. Its Background Tasks
layout was not stale browser cache: the redesign remained uncommitted in
`/private/tmp/integral-core-hardening`, alongside the earlier reminder changes.
This candidate recovers the applicable source onto the current branch without
copying its old runtime environment, task evidence folders or package snapshots.
The original checkout and runtime data remain preserved.

The candidate combines:

- Bounded operation-output references and exact current function/declaration,
  workspace and dependency authorization on initial, cached and receipt results.
- Canonical Comment/Attachment parent containment, current Entry admission,
  unsupported object refusal and whole-output denial instead of partial leakage.
- Current human Auth/profile receipt aliases, immutable original successful
  receipts, bounded direct alias markers and old/new worker serialization.
- Durable read-cache queries using an actual database limit, conflict/corruption
  refusal and independent returned dictionaries.
- Lightweight proposals for new standalone literal reminders through the existing
  broker/staging/approval path. Record-dependent or mixed tasks retain the full
  operational path. Literal delivery uses the existing durable work lease and a
  deterministic message ID; no model call is needed for each reminder delivery.
- Current single-reminder approval confirmation from the saved routine, and
  explicit approval-outcome context for the general operational continuation.
- Compact active/attention workspace tasks, hiding completed/chat/routine history,
  preserving App-job labels/progress, pagination and workspace-switch fencing.
  The scheduled-routines anchor also resolves during client-side navigation.

These are generic framework mechanisms. No Business prices, plans, App slugs,
App-specific field mappings, or domain UI are introduced.

## Qualification record

Focused pre-consolidation evidence:

- 22 PostgreSQL alias/recovery cases; 69 existing PostgreSQL operation/authority
  cases; 126 neighboring operation tests: all passed.
- Recovered reminder tests: 48 passed and one PostgreSQL-only skip with the default
  store; all 12 reminder tests passed with PostgreSQL.
- Recovered UI: six focused tests and the earlier 1,817-test frontend run passed;
  a final full frontend run includes the added navigation test.

Final source qualification (the backend and frontend file manifests were
rechecked against the tested source before commit):

| Gate | Result | Local evidence |
| --- | --- | --- |
| Full default backend, four workers, TESTING=1 / DEBUG=false | 6,429 passed, 690 skipped, zero failures/errors; 7,119 cases, 1,201.695 seconds | `/tmp/core-pipeline-complete-full.xml` and `.log` |
| CI-equivalent smoke selection, two workers | `make verify-ci` exit 0 | `/tmp/core-pipeline-complete-ci.log` |
| Receipt-error cases outside smoke selection, same CI environment | Four passed | `/tmp/core-pipeline-receipt-ci-extra.xml` |
| PostgreSQL operation/reminder contracts | 104 passed, no skips/failures/errors | `/tmp/core-pipeline-qualified-native.xml` |
| Final ordinary-routine/reminder compatibility, PostgreSQL | 14 passed, no skips/failures/errors; overlaps the preceding set | `/tmp/core-reminder-agent-compat-native.xml` |
| Full frontend | 320 files, 1,820 passed | `/tmp/core-pipeline-typed-final-frontend.log` |
| TypeScript and production frontend build | Passed | `/tmp/core-pipeline-table-typed-build.log` |
| Pinned pre-commit framework on all 43 changed files | Passed | `/tmp/core-pipeline-complete-hooks.log` |
| Staged architecture guards | Passed | `/tmp/core-pipeline-complete-guards.log` |
| Public Core wheel build/import proof | Passed | `/tmp/core-pipeline-complete-artifact.log` |

The default-store skips include PostgreSQL-only, explicit restore and opt-in
journeys. The targeted native runs qualify the listed contracts; they do not
prove every skipped journey. CI-equivalent smoke is not the entire remote PR
workflow. The public Core wheel proof is not independent SDK/external-App/ASGI
qualification. The 104 and 14 native counts overlap and must not be added.
Earlier interrupted and failing runs are retained below as historical evidence,
not relabeled as passes. Evidence paths are local execution records.

The user subsequently authorized reloading the local Business deployment after
qualification and push. Runtime/source readback and browser acceptance remain
required. No merge, live-provider qualification or full-goal closure is claimed.

## Explicit remaining limitations

- Result metadata author/type/model/public identity projection still needs its own
  safe field contract. Unsupported references are withheld conservatively.
- Opaque confirmation of a known applied operation when current result disclosure
  is denied (including preflight revocation) remains a separate recovery contract.
- The remaining executor families, external-effect reconciliation, restore/session
  journeys, replica-wide limits, privacy/migration and load gates remain open as
  recorded in the hardening execution ledger. Do not treat this push as completion
  of the full 2026-10-14 review goal.
- Task history is filtered after a bounded API page. Pagination remains available
  even if a page contains only hidden history; server-side attention filtering is
  not implemented here. Routine execution history stays in each routine's Activity.
- Earlier saved agent-mode routines are not automatically converted to literal
  reminders. New reminder proposals require review and approval.


## Table column labels: browser reproduction and fix

The user reported empty headers on the live Income Log table. Read-only inspection
of its View designer showed five valid `custom_fields.*` column paths with no
`label` attributes. The schema inspector displayed the expected field labels.
The shared renderer used only `column.label`, so desktop headers, visibility
buttons and mobile key/value labels were empty. This was a display-contract gap,
not loss of row values or evidence of a corrupt saved view.

The shared TableWidget now resolves explicit nonblank column label, then the
current field schema label, then platform defaults or a readable field name.
Explicit labels and value paths remain unchanged. No saved view or App-specific
code needs migration. Two regression cases failed before the fix; all seven
TableWidget cases passed after it (`/tmp/core-table-labels-red.log`,
`/tmp/core-table-labels-green.log`). The final full frontend and build gates passed; deployed browser readback
remains required.


## Final admission and fixture follow-through

The reminder stream now repeats the durable output-authority check after any
proposal event and before releasing confirmation text. A new probe yielded one
tool event, revoked the mock lease, and reproduced the old unchecked text release
(`/tmp/core-reminder-release-red.log`). The corrected stream refuses the next
release; the 50-case reminder/adaptive set passed with one PostgreSQL-only skip
(`/tmp/core-reminder-release-green.xml`). This uses the existing work admission
mechanism, without a new authority cache or retry path.

The superseded broader run also exposed an old perimeter test's fabricated
linked Auth ID. That fixture now uses the actual test Auth identity/profile for
operation dispatch, retaining the exact entry-read revocation and no-handler-
rerun assertions. All 21 perimeter cases passed (`/tmp/core-pipeline-perimeter-fixture.xml`).
The superseded runs are recorded at `/tmp/core-pipeline-final-full.log` and
`/tmp/core-pipeline-final-ci.log`; they were interrupted and are not passing gates.
Final source qualification uses the gate paths in the table above.

The corrected, typed table implementation passed the full 1,820-test frontend
suite, all seven focused TableWidget cases, and TypeScript/production build.
The schema display label is `OperationalModelFieldSpec.name`; the fallback uses
that existing contract. Source remains frozen for the final gates.

## Frozen candidate gate updates

The final PostgreSQL run completed with 104 passed, no failures/errors/skips
(`/tmp/core-pipeline-qualified-native.xml`). It includes the post-proposal
stream-release regression. This is disposable native-store qualification, not
a live provider or Business browser acceptance claim.


## Ordinary AI routine compatibility follow-through

A further compatibility case reproduced an incorrectly selected literal-reminder
confirmation for a normal agent-mode routine approval. The fast path must also
require the actual staged payload's explicit `delivery_mode: reminder`; absence
keeps the existing agent-mode behavior. The isolated correction and regression
are in `/private/tmp/core-recovered-reminder-pipeline`. The 51-case reminder/
adaptive set passed with one PostgreSQL-only skip after the fix
(`/tmp/core-reminder-agent-compat-green.xml`). This correction is reconciled into the final candidate and covered by the final
full-source rerun and final CI-equivalent gate. The earlier baseline runs are
not evidence for this subsequent correction.


## Full-source fixture correction

The pre-compatibility full run completed with 7,118 cases: 6,424 passed,
690 skipped, four failed, no errors (`/tmp/core-pipeline-qualified-full.xml`).
All four failures were receipt-boundary unit tests using a fabricated user/App;
they stopped at current identity admission before the intended error seam.
The tests now use the rooted owned-App helper and actual authenticated profile.
Their original idempotency conflict, incomplete receipt, unavailable transaction
and required-key assertions remain unchanged. All four corrected tests passed
(`/tmp/core-receipt-boundary-rooted-fixed.xml`). No production identity check was
weakened. The final full-source rerun is `/tmp/core-pipeline-complete-full.xml`;
the final CI-equivalent selection is `/tmp/core-pipeline-complete-ci.log`.



## Final CI-equivalent gate

The corrected production candidate passed `make verify-ci` with TESTING=1,
DEBUG=false and two xdist workers (`/tmp/core-pipeline-complete-ci.log`, exit 0).
All 17 changed production files matched the frozen final main manifest. The
unmarked four-case receipt-error module is outside the smoke selection; its
rooted fixture correction is qualified explicitly in that environment as well
as by the final full suite. The mirror was reconciled after its CI process
terminated and now matches all 32 changed backend files. No failed guard was
bypassed and no selected test was removed to obtain a pass.


The four rooted receipt-boundary cases also passed in the CI environment in the
reconciled mirror (`/tmp/core-pipeline-receipt-ci-extra.xml`, four passed, no
failures/errors). This complements the smoke-selection gate rather than
misrepresenting those unmarked cases as selected by `make verify-ci`.
