# October 14–19 QA fixes

Scope: the four Failed QA items from October 14 and eight new October 19 items in the supplied QA document. The fourteen Passed QA items remain regression coverage. The original qualification used **0.1.1rc25**; the follow-up is based on main **0.1.1rc26**; these changes do not publish or deploy a release.

## Reconciliation

| ID | Issue | Implementation and focused evidence |
|---|---|---|
| Q01 | Chat paste/caret | Plain drafts use native textarea rendering. Entity-highlight mirrors follow textarea width and scrolling. Component regression and isolated-browser multiline editing keep insertion in the middle of a long draft. |
| Q02 | Kanban deletion retains a select option | One guarded configuration command checks complete stored records, scalar/array membership, literal/App-setting defaults, migration barriers and canonical model authority. It removes the unused enum and affected board columns together, refuses shared model edits and preserves another type's valid options. API tests exercise persisted readback, hidden records, defaults, last option, multiple boards and another principal. |
| Q03 | Feed entry menu clipping | Feed cards use the shared portal menu, including keyboard navigation and dismissal. Browser inspection confirms fixed positioning under `body`, both actions visible and bounds inside the viewport despite an overflow-hidden ancestor. |
| Q04 | Saved Track Template deletion | The pictured template is a library model. Its picker links to model management, with deletion offered only by an exact server capability check. Deletion rechecks permission and refuses models attached by reference. Tests cover denied authority and preservation of attached models. |
| Q05 | App/Entry approval presentation | App updates use the Track-style readable name and before/after fields. Entry field patches show each changed value's before/after state with scoped relationship labels. Generic staging tests cover both forms. |
| Q06 | Tags remain in a draft / existing tags change | Ordinary additions use one approved live tag batch rather than a full draft revision. `create_tag` now joins the existing canonical model-command receipt fence and current-authority readback. Interrupted PostgreSQL batches preserve existing IDs/colors and resume committed steps without duplication. Seeded/legacy names participate in duplicate validation. Explicit model drafts retain separate reviewed publication. |
| Q07 | Remove Enhance this view | The View action and its target-specific handoff variant are removed. Follow-up retires Track/Entry enhancement too, including the recommendation tool/service, legacy event and skill instructions; explicit user-directed model authoring remains. View action tests assert absence while retaining layout editing. |
| Q08 | Tag picker does not reopen | Clicking the already-focused input explicitly reopens results. Keyboard dismissal restores focus without reopening; each picker has its own results identity. Component and browser checks select another tag without typing or changing windows. |
| Q09 | Generic tag errors | Client validation explains name length/duplicates; canonical backend messages remain visible, failed input stays available for correction, and pending creation cannot submit twice. |
| Q10 | Crowded tag filters | Tag choices occupy one horizontally scrollable row. Selection count and clear action remain outside the scrolling choices. Browser inspection with 25 tags confirms a single 32px choices row. |
| Q11 | Global tag filtering | A permission-scoped workspace tag selector qualifies names by their owning Track. Exact tag identity reaches database filtering before keyset pagination, including the feed alias/fallback. Tests distinguish same-named tags and find an older matching record behind a newer nonmatching record. Metadata events and approval completion refresh open tag/schema controls. |
| Q12 | Stop leaves backend busy | Stop captures the observed backend target, persists retry identity, requests conditional cancellation and waits for authoritative database activity reads. The composer remains busy until acknowledgement; failures are visible and retryable. Local cancellation nudges the provider while durable worker cancellation remains authoritative. Tests cover failure, delayed acknowledgement, newer turns, bounded polling and fresh reads. |

## Qualification

Evidence is completed in `tasks/todo.md` before the patch is committed. Validation combines the full frontend/default backend suites, focused PostgreSQL commands, type/build/lint checks, staged substrate guards and CI-faithful smoke checks.

The browser checks use isolated components with stubbed API data. HTTP and command tests use isolated real JSON/PostgreSQL stores. A provider-backed production conversation, Google Docs' clipboard integration and deployment acceptance are separate from this local qualification. No live business records were changed for testing, and no named QA prompt was copied into regressions.

The database fixture runs in disposable RAM storage because the local Docker disk is full. Only old, unused build cache was reclaimed; existing containers, images and application volumes were preserved.


## Complete follow-up reconciliation

The live document was reread on 2026-10-19. Its current scope is all 18 October
14 items and all 8 October 19 items; some statuses have changed since the first
qualification. See `tasks/qa-followup.md` for the complete item inventory and
current acceptance status. This source now includes main's rc26 installer fix.
No release bump or production acceptance is claimed by this working patch.

Enhance is retired across Entry, Track and View surfaces, including its component,
legacy chat event, recommendation binding/manifest/service, unused routing helper
and active skill instructions. Conversation previews wait 700 ms and cancel on
leave, click, blur, scroll, resize, Escape or unmount; authorized full titles and
metadata remain available after that deliberate hover/focus.

Real acceptance found and fixed two further Stop failures that mocks missed:
Object query mapping searched a nonexistent nested ID, and cancellation cleanup
tried to pass the ordinary effect fence after cancellation had closed it. The
fresh read now uses the stored WorkItem key plus its canonical identity/scope.
Cancellation settlement saves only committed public output and releases work/run
state and admission through the existing leased transaction. Ordinary tool writes
remain blocked. PostgreSQL chat-worker/submission contracts: **75 passed**.

Actual frontend/API/PostgreSQL/provider acceptance passed blank App creation and
continuation, ten live tags with retained existing identities/colors, App/Entry
approval/readback, Kanban guards/removal, template deletion without live data
loss, picker reopening, preferences, field controls, hidden/restored/reordered
views, menu containment, delayed conversation previews, cancellation plus a new
message in the same conversation, and environment-key OpenAI chat with no stored
workspace model credentials. See tasks/qa-followup.md for exact records and limits.

The final tag presentation uses a shared canonical UI primitive, soft validated
tints and explicit selection checks. Entry-tag filters live only inside Filters,
with active counts and Clear all; the Track header is reserved for actual Track
metadata. The panel stays within the viewport as selections change, at 320/768/
1024/1440 pixels. Screenshots were inspected in dark/light themes.

Final frontend qualification: **338 files / 1,936 tests passed**, production build
and TypeScript passed, ESLint zero errors / 464 existing warnings. All 16 staged
source guards and pre-commit formatting/import/lint/mypy/attribution checks passed.
The built local patch wheel excludes every retired Enhance runtime entry point.
Full default backend: **6,982 passed / 961 skipped**, no failures/errors. Local
CI-faithful smoke, Core-only and extension-contract lanes passed. The broad
PostgreSQL lane is recorded in tasks/qa-followup.md with its explicit restore,
slow-history and Docker backup-drill limitations.

Google Docs' system clipboard formatting and production deployment remain
separate acceptance gates. Local PostgreSQL 14 has vector retrieval explicitly
disabled for these non-retrieval scenarios. No live Business records were changed.
