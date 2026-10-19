# October 14–19 QA fixes

Scope: the four Failed QA items from October 14 and eight new October 19 items in the supplied QA document. The fourteen Passed QA items remain regression coverage. Core version remains **0.1.1rc25**; these changes do not publish or deploy a release.

## Reconciliation

| ID | Issue | Implementation and focused evidence |
|---|---|---|
| Q01 | Chat paste/caret | Plain drafts use native textarea rendering. Entity-highlight mirrors follow textarea width and scrolling. Component regression and isolated-browser multiline editing keep insertion in the middle of a long draft. |
| Q02 | Kanban deletion retains a select option | One guarded configuration command checks complete stored records, scalar/array membership, literal/App-setting defaults, migration barriers and canonical model authority. It removes the unused enum and affected board columns together, refuses shared model edits and preserves another type's valid options. API tests exercise persisted readback, hidden records, defaults, last option, multiple boards and another principal. |
| Q03 | Feed entry menu clipping | Feed cards use the shared portal menu, including keyboard navigation and dismissal. Browser inspection confirms fixed positioning under `body`, both actions visible and bounds inside the viewport despite an overflow-hidden ancestor. |
| Q04 | Saved Track Template deletion | The pictured template is a library model. Its picker links to model management, with deletion offered only by an exact server capability check. Deletion rechecks permission and refuses models attached by reference. Tests cover denied authority and preservation of attached models. |
| Q05 | App/Entry approval presentation | App updates use the Track-style readable name and before/after fields. Entry field patches show each changed value's before/after state with scoped relationship labels. Generic staging tests cover both forms. |
| Q06 | Tags remain in a draft / existing tags change | Ordinary additions use one approved live tag batch rather than a full draft revision. `create_tag` now joins the existing canonical model-command receipt fence and current-authority readback. Interrupted PostgreSQL batches preserve existing IDs/colors and resume committed steps without duplication. Seeded/legacy names participate in duplicate validation. Explicit model drafts retain separate reviewed publication. |
| Q07 | Remove Enhance this view | The View action and its target-specific handoff variant are removed. Track/Entry enhancement and general view authoring remain available. View action tests assert absence while retaining layout editing. |
| Q08 | Tag picker does not reopen | Clicking the already-focused input explicitly reopens results. Keyboard dismissal restores focus without reopening; each picker has its own results identity. Component and browser checks select another tag without typing or changing windows. |
| Q09 | Generic tag errors | Client validation explains name length/duplicates; canonical backend messages remain visible, failed input stays available for correction, and pending creation cannot submit twice. |
| Q10 | Crowded tag filters | Tag choices occupy one horizontally scrollable row. Selection count and clear action remain outside the scrolling choices. Browser inspection with 25 tags confirms a single 32px choices row. |
| Q11 | Global tag filtering | A permission-scoped workspace tag selector qualifies names by their owning Track. Exact tag identity reaches database filtering before keyset pagination, including the feed alias/fallback. Tests distinguish same-named tags and find an older matching record behind a newer nonmatching record. Metadata events and approval completion refresh open tag/schema controls. |
| Q12 | Stop leaves backend busy | Stop captures the observed backend target, persists retry identity, requests conditional cancellation and waits for authoritative database activity reads. The composer remains busy until acknowledgement; failures are visible and retryable. Local cancellation nudges the provider while durable worker cancellation remains authoritative. Tests cover failure, delayed acknowledgement, newer turns, bounded polling and fresh reads. |

## Qualification

Evidence is completed in `tasks/todo.md` before the patch is committed. Validation combines the full frontend/default backend suites, focused PostgreSQL commands, type/build/lint checks, staged substrate guards and CI-faithful smoke checks.

The browser checks use isolated components with stubbed API data. HTTP and command tests use isolated real JSON/PostgreSQL stores. A provider-backed production conversation, Google Docs' clipboard integration and deployment acceptance are separate from this local qualification. No live business records were changed for testing, and no named QA prompt was copied into regressions.

The database fixture runs in disposable RAM storage because the local Docker disk is full. Only old, unused build cache was reclaimed; existing containers, images and application volumes were preserved.
