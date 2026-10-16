# On-demand route and view context capability

Implemented locally on `codex/harness-hardening-qualification`, based on Core `8af5d9ca1605154435966ac0e6491ff3330cdf50`. The earlier disclosure-hardening changes remain intact. Locally deployed to Business on 2026-10-13 after user authorization; see the local deployment report below. No commit, push, user-record mutation or existing PR update was performed.

## What changed

- Reuses `integral_get_page_context`; no duplicate tool. Its schema is immediately available when declared in the supplied catalogue. Invocation still goes through the original capability snapshot and live broker checks.
- Native instructions contain only an availability/use hint, never an inline page snapshot. The model calls the capability as needed for “this App/view/record,” visible rows and page-orientation questions. A completed page read permits an orientation answer without unrelated skill discovery; simply exposing its schema does not admit arbitrary operational prose. Normal operational discovery and write/approval checks remain in place.
- Returns `current_turn`, `last_known`, `absent`, `invalid` or `inaccessible` provenance, the bound run identity and explicit client-reported/non-live-screen labels. A bound turn missing its snapshot cannot consult the thread's mutable historical page.
- Validates the existing typed snapshot, removes hostnames/fragments/secret query keys and unrelated metadata, checks route/focus/view-pointer consistency, and limits visible lists to eight. `stub` excludes visible records before authorization/lookups.
- Focus and visible App/Track/Entry/View references receive current workspace and ACL checks before canonical labels are returned. Views use their actual parent Track/App authority. An inaccessible primary resource returns no page details; inaccessible secondary focus is removed. Visible rows receive canonical titles and entry navigation anchors; counts remain client-reported, not governed query results.
- Binds implicit focus from a consistent accepted snapshot. Invalid/conflicting snapshots supply no implicit target; explicit operation arguments remain separate. Explicit real App IDs now outrank incidental focus for list/add-track calls, and unresolved names cannot be redirected to a different open App. Existing new-App batch placeholders remain supported.
- Contextual public-help questions stay on the native capability path rather than being intercepted by the fixed generic-help response.
- Existing frontend publishers already supplied View identity/name/type and Entry-dialog precedence. Added the active App section and supported string-valued view grouping metadata, plus regression tests for Kanban/table/calendar/gallery and App pages with no saved View.

## Qualification

- **860 passed, zero failures/errors/skips** in the PostgreSQL-backed combined native-harness, page-context, broker, permission, scope, approval, recovery and contract cohort; 204.33 seconds. Evidence: `tasks/route-context/evidence/postgres-final.xml`.
- **1,739 frontend tests passed**, 307 files; 83.85 seconds. Includes five additional selected-view/section cases. Frontend TypeScript passed; targeted ESLint had zero errors and 13 existing warnings.
- Pinned pre-commit formatting, import order, lint, convention guard and backend mypy passed (711 source files). `make capability-map` ran; generated output was unchanged.
- The two recorded prompts (“What page is this,” “please add dummy data to this app”) were exercised through the native model/tool composition with a controlled model and broker fixture. They obtained page identity using the page-context capability, without unrelated discovery or mutations. This tests harness wiring, not a live provider's semantic performance or a completed seeding journey.
- Actual PostgreSQL resource checks resolved canonical App/Entry labels and refused a foreign workspace. Separate cases cover revoked/missing access, forged labels, inconsistent pointers, absent/stale context, concurrent tabs, scope-bound dispatch, visible-row caps, secret URL fields and private-prompt controls.

## Preserved boundaries and limits

UI context grants neither permissions nor approval. It describes the accepted message's reported screen, not current DOM access; canonical labels/ACLs are checked at read time. Complete record values and counts still require governed reads. Unknown page labels and filters remain untrusted client data. Legacy snapshots without an active bound workspace are explicitly marked `resource_verification: not_performed`; the native broker path always carries authenticated scope.

The source change is not running in the existing Business stack. Post-update live-provider/browser and full approved seeding/readback qualification remain release gates. The original screenshots/chat debug were evidence of the preceding runtime's gap, not proof of deployment. No browser or provider outcome is inferred from green source suites.

## Local deployment follow-up

See [Business deployment and live smoke](2026-10-13-local-business-hardening-smoke.md). Live read-only App, saved table/Kanban view, explicit target and disclosure probes passed; approved mutation/readback remains unqualified.
