# Complete October QA follow-up

Source: live export of the supplied QA document, read 2026-10-19. Scope is all
18 October 14 items and all 8 October 19 items, plus complete Enhance removal
and delayed conversation-title previews. Previous qualification of #160 was
not deployed acceptance. This pass uses current main (rc26) plus #160 and the
follow-up changes; no version bump until qualification passes.

## Work

- [x] Read the live document; reconcile changed QA statuses instead of assuming the prior list is current.
- [x] Integrate rc26 installer fix without undoing it or downgrading the version.
- [x] Remove Track/Entry/View Enhance UI, prefill event, recommendation tool/binding/service and skill instructions.
- [x] Delay full conversation previews by 700 ms; cancel pending previews on navigation/click/leave/Escape/scroll/resize/unmount.
- [x] Production frontend build and final full frontend suite (338 files, 1,936 tests).
- [x] Full backend suite and post-removal tool/capability checks.
- [x] Current-source PostgreSQL contracts and CI-faithful lanes.
- [x] Real local browser/API acceptance for every failed/new QA flow (clipboard integration limit below).
- [x] Recheck the 18 October 14 and 8 October 19 acceptance criteria individually.
- [x] Final source, format, typing, guard and artifact checks.

## Preserved invariants

I-GRAPH-01/02: no relationship or record deletion is introduced by retiring UI/tools.
I-ROLE-01/02 and scoped access: ordinary and approval operations retain current
actor/resource checks. I-APPROVAL-01/02/05: live tag receipts and model publication
remain approval-bound. I-CHAT-01/PAR-01: cancellation acknowledgement and next-turn
admission must be checked against current authoritative work state. Removing
Enhance does not add an alternative automatic model mutation path.

## Acceptance inventory

| Source | Item | Evidence to verify |
| --- | --- | --- |
| Oct 14 #1 | Paste/caret editing | Real multiline composer editing, middle insertion, entity references |
| Oct 14 #2 | Existing App/Track/Entry updates | Readable approvals and actual update readback |
| Oct 14 #3 | Same-name workspace invitations | Existing invitation regression plus acceptance identity |
| Oct 14 #4 | Blank App instruction | Minimal blank App creation without added Tracks |
| Oct 14 #5 | Kanban status removal | Delete unused column/enum; deny when records/defaults still use value |
| Oct 14 #6 | Entry menu clipping | Portal positioning and keyboard actions |
| Oct 14 #7 | Details panel persistence | Existing preference regression |
| Oct 14 #8 | Private resource defaults | Existing direct/template/agent privacy and access regressions |
| Oct 14 #9 | Tag colors/filtering | Existing colors retained and correct tagged records |
| Oct 14 #10 | Hidden View dropdown | Existing hide/show action regression |
| Oct 14 #11 | Add-fields Save style | Enabled-state regression |
| Oct 14 #12 | Core field ordering/type safety | Field-order and last/default-type regressions |
| Oct 14 #13 | View ordering | Tab order regression |
| Oct 14 #14 | Track template deletion | Picker management and guarded deletion; installed models preserved |
| Oct 14 #15 | Named View type identity | View-type display regression |
| Oct 14 #16 | Conversation title/menu | Delayed full title, distinct action menu |
| Oct 14 #17 | Resume approved builds | Existing recovery and host-outcome continuation regressions |
| Oct 14 #18 | Environment model key fallback | Real provider turn with only platform environment key |
| Oct 19 #1 | App/Entry approval presentation | Readable title and per-field before/after values |
| Oct 19 #2 | Complete live tag additions | All approved tags/colors persisted, prior IDs/colors unchanged |
| Oct 19 #3 | Enhance removal | No controls/event/service/tool remain in active source |
| Oct 19 #4 | Repeated tag picker | Select, click again, select without typing |
| Oct 19 #5 | Tag validation | Specific duplicate/length errors, editable failed input |
| Oct 19 #6 | Compact tag filters | Single scrollable row and retained clear action |
| Oct 19 #7 | Global tag filtering | Track-qualified identity before pagination and current read authority |
| Oct 19 #8 | Stop backend release | Real in-progress turn cancellation and immediate subsequent request |

## Limits / status

Local acceptance is complete. Synthetic local data only; no production data or
external messages are used. Browser API and model calls must use current source,
not a stubbed component fixture. The local acceptance PostgreSQL instance is
isolated on port 55447; semantic embeddings are explicitly disabled because
these QA scenarios do not exercise retrieval. Production uses pgvector.


## New failure found by real acceptance

Stop returned 404 while the real provider was running. `WorkItem` is a
`jvspatial.Object`; its public `find_one({"id": ...})` maps that key to
`context.id`, which is absent. The previous mocked fresh-read test hid this.
A direct storage reproduction confirmed raw/typed finds were empty even though
the canonical WorkItem row existed and hydrated correctly. The fix queries the
indexed `context.work_item_id` and rechecks canonical object ID plus the complete
owner/workspace/thread/kind tuple. New persisted-object and wrong-identity
regressions cover the case; final real cancellation retest remains pending.

## Real acceptance evidence so far

- Synthetic workspace `n.Workspace.9e62459794e4474795c21155`. No Business data.
- Provider-backed blank App proposal/build persisted `n.WorkspaceApp.3a75995430064f468e63880d` with zero Tracks; continuation worked.
- Existing App and Entry rename approvals displayed title and old/new names, and both changes persisted with original IDs.
- Ten approved live tags persisted on `n.Track.115216ad8cb84d5095fe1964`; original tag ID and `#456789` color were preserved.
- Used Kanban option refused; moving the record allowed removal from both the live schema and board.
- Global tag identity plus limit=1 found the correct older record despite a newer unrelated record and a same-name tag on another Track.
- Real duplicate/length checks returned specific JSON 409/400 messages.
- Real Entry editing selected two tags by reopening the picker without typing, retained their distinct colored borders/backgrounds, and persisted them.
- Hiding Entry details survived close/reopen. Entry and Track owner controls contain no Enhance action.
- Conversation hover produced zero immediate previews, then the full title/metadata card; Escape dismissed it.
- A three-line real composer draft accepted an insertion in the middle and kept selection at 111 of 149 characters.
- Saved template deletion through the actual model page removed the reusable model while retaining live Track, record and all 11 tags.

## Cancellation follow-up and current proof

The second real Stop failure was in cleanup: the worker tried to append its
terminal error and transcript through the ordinary effect fence after the
persisted cancellation flag had correctly closed that fence. The worker then
exited without a terminal WorkItem or admission release. Cancellation now uses
the existing leased terminal transaction to save only already committed public
output, settle the WorkItem/AgentRun, and release both admission reservations.
Business/tool writes remain blocked after cancellation. Wrong principal,
workspace, thread and stale lease regressions preserve that boundary. Stop's
status polling backs off from 500 ms to 2 seconds with the same 30-second limit.

Real browser proof: activity reported running for WorkItem
`fdd2b6a640b8c09b1591ceeeb174ee192b3a7557faa93f7f426c18c9ecf09493`, conditional
cancel returned 200, and subsequent activity reported `running: false`.
Persisted readback confirmed `cancelled`, cancellation requested and cleared
lease. The same conversation then accepted and answered the next message.

Additional current-source browser checks:

- Feed entry menu is fixed under `body`, both Edit/Delete visible and inside the viewport; keyboard navigation and Escape work.
- Hiding a saved board removes its tab; its action becomes Show in tab strip, which restores it. Move earlier changes the actual tab order; the original order was restored.
- Named board displays its Kanban type. Core Title/Body fields are visible and protected. Add-field Save changes from disabled to enabled with contrasting readable colors when a valid name is entered.
- Environment-key acceptance used `openai/gpt-4.1-mini` on the same real runtime. There were zero saved UserModelCredential records, the route was `platform`, and the browser received the requested reply. A send during the deliberate API restart returned 502; the subsequent normal send succeeded once startup finished.
- Final full frontend run: **336 files / 1,929 tests passed**. Production build and TypeScript passed; ESLint has zero errors and 464 existing warnings.
- All 16 staged source guards and pre-commit formatting/import/lint/mypy/attribution checks passed. Reproducible local rc26 wheel build/import passed; this is an unpublished patch artifact, not the public rc26 release.

- Real compact tag filter choices occupy one 32.5px scrollable row; count and Clear tag filters stay outside that row. Selecting and clearing actually filter the persisted feed.

## Existing Passed QA regression mapping

All already-Passed items are retained in the complete suite rather than copied
into query-specific fixtures: same-name invitation adoption is parametrized in
`test_invitations.py`; private defaults in `test_visibility_defaults.py`;
base-field persistence/order and default/final type protections in
`test_crud_entry_types.py`; enabled field saves and field identity in
`FieldEditorPanel.test.tsx`; tab identity/order in `savedViewTabs.test.ts`;
view actions in Track settings; panel preferences in `EntryDetailPanel.test.tsx`;
and credential routing in `test_model_credential_resolver.py` plus native route
contracts. Existing recovery/host continuation contracts remain in the full
backend and PostgreSQL lanes. Real browser evidence above additionally checks
existing updates, tags, details, hidden tabs, field controls, view order/type,
approval continuation, conversation previews, and environment-key chat.

Google Docs' actual system-clipboard formatting is not automated here; the real
multiline composer and its middle insertion are checked, along with paste/caret
component regressions. PostgreSQL 14 runs locally without vector retrieval;
production PostgreSQL/vector qualification is a separate deployment gate.

## Tag presentation follow-up

Requested during qualification: remove the redundant visible Tags prefix and
make tags more presentable. A shared TagChip now renders record headers, feed
rows, selected form values and filters with a soft validated color tint, small
color dot, readable theme text and consistent spacing. Filters use a checkmark
for selection and a compact Clear (count) action, retaining accessible control
names and exact tag IDs. No stored tag names, colors or relations changed.
The filter row uses PageSection gutters to align with its heading and content.

Focused tag/entry checks: 29 passed. Actual browser screenshots were inspected
in dark and light themes. At 320, 768, 1024 and 1440 CSS pixels the tag filter
stays inside the viewport; choices scroll horizontally, with Clear visible at
320px. Selected/unselected chip buttons align at the same 28px height. Final
frontend/build/lint gates are rerunning for this additional requested change.


### Final requested placement

Entry-tag filters now live only inside TrackFilterStrip's Filters dialog, beside
entry type. TrackDetailPage supplies the complete loaded entry set through its
main column; filter choices do not disappear when results are filtered. The
Filters badge includes tag selections, Clear all resets tags/type/search, and
closing the panel hides the controls. The Track header contains no entry filter
row and remains reserved for actual Track metadata. Wiki views, which have their
own browsing interface, do not inherit an invisible entry-tag filter.
The earlier gutter-aligned header-row screenshot was superseded by this explicit
user direction. The shared chip styling remains in record metadata and editors.
The new primitive is in canonical `src/ui`, with no drift-guard exemptions.

Final placement browser proof: entry-tag controls exist only in the Filters
dialog; the header has none. With two selected tags, the active count is 2 and
the real feed contains the matching record. The fixed popup is clamped to the
viewport after count/clear controls move its anchor; measured containment passed
at 320, 768, 1024 and 1440 pixels. Escape restores focus to Filters. A regression
covers narrow positioning, selection changes, resize and keyboard dismissal.
Clear all was used to restore the synthetic page; its original dark theme and
1728×875 CSS viewport / device scale 2 were restored.


Final frozen frontend source: **338 files / 1,936 tests passed**, production
build/TypeScript passed, ESLint zero errors / 464 existing warnings. The live
Google document was exported again near the end of qualification: all 2,444
lines were unchanged from the initial read. No newly added QA item was omitted.


## Completed broad validation

- Full default backend: **6,982 passed / 961 skipped**, zero failures/errors
  (7,943 collected outcomes; JUnit recorded). PostgreSQL-only scenarios skip in
  this JSON/default lane and are qualified separately below.
- Local CI-faithful smoke, Core-only and extension-contract lanes: exit 0.
- Final reproducible Core wheel/import proof: exit 0; SHA256
  `1998723eeb7a3d6f9fa88967d1e6ebc39e8bca18b964179529d98ca34decfad0`.
  A separate built-wheel inspection verifies no retired service/function/tool/binding.
- Fresh normal account login after the browser exercises succeeded; the actual
  conversation activity endpoint reports idle, with no active work or turn ID.

PostgreSQL contract limits: the explicit restore opt-in and two slow history
cases retain their normal skips. The backup/restore drill skipped because its
Docker pg_dump container could not start on the full Docker disk. This run does
not qualify restore rehearsal; no existing containers or volumes were removed.


Broad `make test-postgres-ci`: **exit 0**, including conditional-state spike,
contract/PostgreSQL cases, App jobs/tool-context ownership, credential delivery,
member provisioning, typed relations/templates and rc26 installer authority.
Restore/drill skips above remain explicit limitations. All follow-up source work
and local gates are complete; package publication and deployed smoke are separate.
