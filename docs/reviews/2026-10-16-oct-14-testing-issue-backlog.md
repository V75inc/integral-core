# Oct 14th Testing Issue Backlog

Source: [Integral Google Doc, “Oct 14th testing” onward](https://docs.google.com/document/d/1DNRtiZ2-_D1fYuUGk4JJEutV3d6-IQ9gedRLkqN88ZA/edit?tab=t.0#heading=h.7oyf83ud0ns)

Prepared from the 18 numbered reports in that section. This is a triage and patch-planning artifact. Each verification target is a proposed acceptance check derived from the reported behavior; implementation status is recorded below.

## Triage order

Prioritize access and data-integrity risks first, then agent reliability, then interaction and presentation issues. P0/P1 are proposed engineering priorities, not severity labels supplied by QA.

| Order | Issue | Priority | Likely area | Patch-ready outcome / verification target |
|---|---|---:|---|---|
| 1 | **#8 App/Track visibility defaults and zero-trust** | P0 — policy review | Access model, app/track creation, templates, resident harness | Confirm the intended visibility policy against the canonical access-control spec. If approved, verify new, template-derived, and agent-created Apps/Tracks default to the authorized private state; verify workspace members without a grant cannot read or mutate them; cover explicit sharing and existing-resource compatibility. The report’s proposed removal of “Inherit” is a policy change and must be reconciled with current workspace visibility/cascade semantics before coding. |
| 2 | **#3 Workspace invitation with duplicate workspace names** | P1 | Invitation identity, accept/redeem, shared-with-me list | Create same-named workspaces for inviter and invitee; invite to one exact workspace; accept must apply to the invited workspace ID. Closing an error or retrying must not consume/hide an unhandled invitation; accepted/declined states must be reflected consistently to both parties. |
| 3 | **#5 Deleting a Kanban column leaves an orphan status** | P1 | View configuration, status fields, entry validation/rendering | Delete a column whose status is unused and one whose status is assigned. The system must either block/confirm reassignment or migrate the entries, and must never leave records with a status that makes them disappear from the board. Verify board and entry editor agree. |
| 4 | **#18 Agent ignores API keys configured in `.env`** | P1 | Model route resolution, credential fallback, chat worker | With no Settings credential and a valid environment key, a new turn resolves and uses the configured provider even when no credential-generation reference is configured. Preserve missing attribution rather than inventing a reference; mandate billing still requires exact attribution. With neither credential source, return a clear actionable error. Verify Settings credentials retain precedence and secrets never appear in logs or responses. |
| 5 | **#2 Generic failure updating an existing App/Tracks/Entries** | P1 | Agent planning, staged tool execution, multi-resource update flow | Replay the supplied Personal Life Manager update against existing records. Verify requested names/values change in place, no duplicate entities are created, and partial failures identify the failing operation and leave a reviewable outcome. Add a regression for the exact reported prompt. |
| 6 | **#17 Approved multi-track App build is blocked by earlier assistant run** | P1 | Chat turn lifecycle, interrupted-run recovery, staging/approval continuation | Reproduce approval followed by build failure and retry in the same conversation. A reviewed/failed prior run must not permanently block a new attempt; preserve or clearly expose prior staged work and prevent accidental duplicate creation. Test retry and new-thread fallback behavior. |
| 7 | **#4 Agent cannot create a blank App** | P1 | Agent intent interpretation, App creation contract, empty App UI | Ask for “Create an app called QATesting,” then explicitly request it blank. Verify the agent creates only the named App, proposes no Tracks/Entries/fields/views, and does not invent a minimum-structure constraint. If a real constraint exists, surface it before approval and offer the smallest valid option. |
| 8 | **#1 Chat input caret moves after paste** | P2 | Chat composer editor and clipboard/paste handling | Paste formatted multiline text, place the caret in the middle, edit/insert text, and verify the caret and selection remain under user control. Cover paste with and without an existing selection. |
| 9 | **#7 Details-panel visibility is not remembered across panels** | P2 | Entry/detail panel state | Hide the panel, open another item/panel, and verify it remains hidden until explicitly shown. Cover close/reopen and navigation between entries; establish whether preference is session-local or persisted and test accordingly. |
| 10 | **#9 Tag colors missing and tag filtering unavailable** | P2 | Tag rendering across views, list filters/query | Apply colored tags and verify consistent color in entry detail, list, and board contexts. Filter by one and multiple tags and verify only matching entries appear; test clearing filters and empty results. |
| 11 | **#6 Entry-view overflow menu is clipped** | P2 | Popover positioning, viewport/layout collision handling | Open the entry “…” menu near viewport edges and in constrained layouts. Verify every action remains visible, reachable, and correctly anchored, including after scrolling/resizing. |
| 12 | **#10 Hidden-view menu actions look disabled** | P2 | View tab-strip visibility menu state | Hide a view and reopen its menu. Clearly show the valid inverse action (“Show in tab strip”) as enabled; verify the action toggles visibility and state remains accurate after reopening. |
| 13 | **#11 “Add Fields to Post” Save button has incorrect disabled styling** | P3 | Popup/button state styling | Verify actual enabled/disabled state and visual, keyboard, and accessibility states agree before/after field changes and save. |
| 14 | **#12 Default Entry Type fields cannot be reordered; deletion guard** | P2 | Entry Type editor and ordering validation | Reorder Title and Details relative to custom fields and verify saved order in the entry editor. Verify the default Entry Type and the final remaining Entry Type cannot be deleted, with clear feedback. |
| 15 | **#13 Views cannot be reordered in the tab strip** | P3 | View ordering configuration and tab strip | Reorder views by the supported interaction, save/reload, and verify order persists and is consistent with any view picker. |
| 16 | **#14 Track Templates cannot be deleted** | P3 | Template management, authorization, deletion lifecycle | Authorized users can remove an unused template; unauthorized users cannot. Verify deletion behavior for templates already used by Tracks (block, detach, or preserve provenance according to product policy) and confirm no dependent Track data is removed. |
| 17 | **#15 Custom view names obscure view type** | P3 | View tab labels and view metadata presentation | Give views custom names and verify their underlying type remains identifiable in the tab strip and relevant menus without changing the custom name. |
| 18 | **#16 Truncated chat titles and three-dot menus are confusing** | P3 | Chat list layout and action-menu affordance | Long titles may truncate while the action menu remains distinct and discoverable. Verify rename/delete/archive controls are not mistaken for title ellipses at narrow and wide widths; use the cited ChatGPT/DeepSeek references only as visual direction. |

## Progress in PR #145

Updated 2026-10-17. See [browser qualification and per-report limits](2026-10-17-pr145-browser-qa.md) for fixture IDs, verified outcomes, and remaining coverage boundaries.

- **#2, #4:** Ollama-backed Agent proposal/approval/build and existing-record updates completed in browser. QATesting has zero Tracks/Dashboards; updates retain the original App/Track/Entry IDs. The exact original #2 prompt was unavailable; a synthetic equivalent was verified.
- **#3, #5–#7, #9–#16:** Implemented fixes and exercised the relevant browser interactions. Additional service/UI regressions cover invitation retries, protected type/template deletion, legacy Kanban statuses, field ordering, and multi-tag matching. The report distinguishes automated coverage from browser coverage.
- **#8:** User approved removal of explicit Track Inherit visibility on 2026-10-17. Apps/Tracks default Private; new Track writes accept Private/Workspace/Public only. Legacy inherited Tracks retain effective access through read compatibility until deliberately converted. Updated architecture and invariants preserve the separate collaborator cascade.
- **#17:** New failed proposal receipts survive rollback under the durable work fence, with narrowly audited no-effect classification. Historical missing-receipt runs remain blocked; the original failed conversation is not claimed recovered.
- **#18:** Environment-only OpenAI dispatch was verified on the preceding commit; Settings-credential Ollama workflows also succeeded during this qualification.
- **#1:** Native multiline plaintext paste/replacement/continued typing passed in the in-app browser. Rich-format paste and Safari remain unqualified; no caret implementation change was justified by this reproduction.

## Scope notes

- **Access-policy decision:** Explicit Track Inherit visibility removal was approved by the user on 2026-10-17 and reconciled in `docs/product/ARCHITECTURE.md` and `docs/INVARIANTS.md`. Existing values are preserved until deliberate conversion; collaborator propagation is a separate rule.
- **Agent reliability cluster:** Issues #2, #4, #17, and #18 all concern the resident agent, but describe separate failure modes (multi-record update, blank App intent, interrupted-run continuation, and provider credential resolution). Keep separate regressions and only combine implementation work after tracing the distinct paths.
- **Data integrity:** Issue #5 needs a defined policy for entries that already use a deleted status. Do not ship a UI-only fix that leaves unreachable values in persisted records.
- **The rest of the document:** Later material includes logs and lettered observations from other testing dates. This backlog is limited to the 18 reports following the “Oct 14th testing” heading; those later notes are not silently merged into this list.

## Suggested patch and test sequence

1. Resolve the visibility policy (#8), then implement and test the access boundary.
2. Reproduce and isolate invitation/data-integrity behavior (#3, #5).
3. Trace agent execution paths and add scenario-specific regressions (#2, #4, #17, #18).
4. Patch the independent UI issues, grouping by shared components after confirming their actual implementations (#1, #6–#7, #9–#16).
5. Run the repository’s required validation for each patch set; for substrate/access changes, include the full applicable suite and the repository `make verify` gate before any commit.
