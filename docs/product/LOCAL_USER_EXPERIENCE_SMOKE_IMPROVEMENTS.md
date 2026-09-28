# Local User Experience Smoke Findings

Date: 2026-09-27
Base: `main` at `d570c47`; work branch: `codex/local-ux-smoke-remediation`
Scope: local install and authenticated browser smoke testing of app scaffolding,
querying, filing, editing, and board views. Product flows were verified in the
browser; repository checks were run separately as commit gates.

## Summary

The initial browser pass found gaps in generated schema and views, repeated
fields, search behavior, and model-change receipts. Local fixes for seven
items and a successful browser recheck of search are recorded below. These
results establish the tested user paths, not a release-qualified build.

## Remediation sequence

| Order | Item | State | Browser evidence / next check |
| --- | --- | --- | --- |
| 1 | Align scaffolded select values and Kanban lanes | Local fix, browser smoked | New Status Alignment Check app has Ready and Needs repair choices and one record in each matching lane. |
| 2 | Stop duplicate Status values from board edits | Local fix, browser smoked | Duplicate Ready rejected; Awaiting parts added to board and form and survived reload. |
| 3 | Track search | Rechecked; no current reproduction | With three records, Pump showed only the matching row, a no-match query showed an empty state, and clearing search restored all three. |
| 4 | Reconcile title and custom Name fields | Local fix, browser smoked | Name Label Check Parts Table and New Part form each show one Name; Demo Bolt appears with Category Fastener. |
| 5 | Avoid invented sample values | Local fix, browser smoked | Blank Sample Check Assets Table shows Demo Generator with Location and Purchase Date blank. |
| 6 | Align bootstrap, signup, and reset password policy | Local fix, browser smoked | Signup rejected an eight-character password with a 12-character message; reset form displays 12. Focused checks cover invalid admin credentials and incomplete profile setup. |
| 7 | Record model-change effect receipts and target existing views | Local fix, browser smoked | Assets Table changed in place with an Update approval; effect receipt exposed Undo, which restored the prior columns after reload. |
| 8 | Clarify email verification notice | Local fix, browser smoked | Notice explains that work remains available; Not now hid it for the session and it stayed hidden after reload. |

Product-flow checks in this pass were performed through the browser. Repository
checks and focused bootstrap checks serve as commit gates; they do not qualify
a packaged release.

## Findings

### Shared Status/Kanban mismatch: two schema-contract gaps

Code inspection points to a reusable platform bug rather than an
Equipment-specific model issue:

1. [`operational_model_compile.py`](/Users/eldonmarks/Briefcase/dev/integral-core/backend/app/services/operational_model_compile.py:3961)
   fills missing Kanban columns with the same four workflow values (`todo`,
   `in_progress`, `in_review`, `done`) without considering the view's
   `group_by` field enum. For a custom select field, those are not necessarily
   valid values. The related batch check in
   [`batch_validation.py`](/Users/eldonmarks/Briefcase/dev/integral-core/backend/app/agentive/batch_validation.py:31)
   considers any `custom_fields.*` group and any non-empty column list valid;
   it does not check that the field is a select or that column keys belong to
   that field's enum. A generic default can therefore pass as a complete
   schema-bound board.
2. When adding a Kanban column for a profile select, the UI creates an opaque
   key such as `col_<id>` and appends that key to the select enum. See
   [`KanbanWidget.tsx`](/Users/eldonmarks/Briefcase/dev/integral-core/frontend/src/components/views/KanbanWidget.tsx:1713)
   and the host sync in
   [`TrackDetailPage.tsx`](/Users/eldonmarks/Briefcase/dev/integral-core/frontend/src/pages/TrackDetailPage.tsx:1097).
   The backend compile path also syncs Kanban column keys into select enums
   ([`operational_model_compile.py`](/Users/eldonmarks/Briefcase/dev/integral-core/backend/app/services/operational_model_compile.py:1357)).
   When the new column is renamed to an existing enum label, the UI maps the
   opaque key back to that label, so the same label appears twice in the
   Status picker. This matches the duplicate `Ready` / `Needs repair` choices
   observed after adding columns with those labels.

The first path explains generic board columns and Unassigned records on
schema-backed boards. The second explains duplicate choices after board-column
creation. Both paths are shared by apps that use profile select fields with
Kanban views. The successful manual board configuration also showed that the
records can be grouped correctly once the board config keys match the field
values.

**Likely correction**: treat a profile select's enum as the source of truth
when creating or binding a board; validate that `group_by` resolves to a
supported field and that column keys match its allowed values; avoid appending
opaque Kanban IDs to a select enum. Keep opaque keys only for system workflow
boards, or define an explicit stable value separate from the display label.

The diagnosis above records the original failure. The first local remediation
now prevents generic workflow lanes from being injected into a board grouped
by a custom field. Scaffold view validation also checks that the grouped field
is a declared select and that the board column keys match its choices. The
board editor now asks for the new lane name before changing the schema, uses
that name as the select value, rejects duplicate lanes, and prevents renaming
select-backed lanes without updating their value.

Browser smoke after these changes: a newly scaffolded **Status Alignment
Check** app had only Ready and Needs repair in its Status picker, with Demo
Ready and Demo Repair each in the matching board lane. Adding a second Ready
lane was rejected visibly. Adding Awaiting parts created one board lane and
one matching Status choice; the lane persisted after reload. This checks the
user-facing path, but does not establish that every legacy board is repaired.

### P1: Scaffolded status options and board are inconsistent

The scaffold proposal explicitly promised a Status field with `Ready` and
`Needs Repair` and a board grouped by those values. After creation, the entry
form showed generic workflow values (`To Do`, `In Progress`, `In Review`,
`Done`) alongside the requested options. Editing later showed duplicated
`Ready` and `Needs repair` values with inconsistent capitalization. The board
initially showed only generic workflow columns and both entries as Unassigned.

The agent acknowledged a status mismatch and staged a repair, but the approval
surface showed “Undo is unavailable because this approval has no recorded
effect receipt.” Browser inspection after the update still found the option
and board mismatch. The resident agent then repeatedly proposed adding a
second Status Board instead of modifying the existing one. Manual board
settings could be used to create `Ready` and `Needs repair` columns, remove
empty generic columns, and align the two items. Changing an item's status
through the entry editor succeeded.

**System improvements**

1. Make scaffold execution reconcile option values and board grouping against
   the requested schema, then read the resulting field and view back before
   reporting completion.
2. Make approval receipts reliably capture affected resources and support
   undo, including when an effect may have applied before receipt recording.
3. Distinguish “add a view” from “edit this existing view” in the model-change
   contract and approval summary.

**Skill improvements**

1. After scaffolding, inspect the actual controlled choices and board columns;
   verify exact values and casing, no duplicates, and that records land in the
   matching columns.
2. When asked to change an existing view, locate and modify it in place. Do
   not stage `ADD_VIEW` unless the user explicitly requested a new view.
3. Treat a missing effect receipt as an unresolved write. Inspect the target
   in the browser before retrying or claiming success.

**Local remediation:** model add/remove operations now emit provenance-linked
change events, and existing views have a distinct `update_view` action. The
stager rejects an Add view when a view with that name already exists, pointing
the resident to the existing ID. The model skill directs in-place edits.
The browser initially exposed a second fault: manifest sync changed the saved
view config after the receipt snapshot. The view API now refreshes the saved
node before recording the effect. A second browser approval showed an Update
card and Undo control; Undo restored the prior table columns after reload.
The older pre-fix approval remains non-undoable, as its recorded snapshot does
not match the later persisted config. A browser-only smoke does not prove the
full range of model operations or concurrent-edit rollback behavior.

### P1: Search appeared not to filter the tested track

With two rows visible, searches for `Demo Pump`, `Pump`, and a unique no-match
phrase left both rows visible in the table. This suggests the track search did
not apply in the tested interactions. It was not verified against a larger
dataset, so this remains a browser-observed failure requiring follow-up.

**System improvement**: make search completion and empty-result state visible;
ensure the query filters the active track view and provide a clear way to
reset it.

**Skill improvement**: include one matching and one no-match query in the
browser smoke flow, and record visible result counts or rows after each.

**Recheck on 2026-09-27:** The same Equipment Table now hides both rows for
`No matching item xyz` and shows “No entries to display.” The separate
Status Alignment Check board also narrowed to Demo Ready for a matching query
and showed empty lanes for a no-match query. The original observation is not
currently reproducible, so no search code was changed. Keep the browser smoke
case to catch an intermittent or mode-specific failure.

A follow-up with three records in Assets Table showed only Smoke Search Pump
for `Pump`, the empty state for `Nothing Matches 93412`, and all three rows
again after clearing search. This closes the small-dataset follow-up while
retaining search as a regression scenario.

### P2: Title and custom Name are both presented as required fields

The generated table displayed `Name` twice: once for the entry title and once
for the custom `Name` attribute. The create form also asked for both values.
This is redundant and confusing for a lay user entering an item's name.

**System improvement**: reconcile a requested name field with the platform's
entry title instead of creating two separate required inputs, or clearly
explain the distinction when both are needed.

**Skill improvement**: check the target entry form and table for title/custom
field duplication before declaring a scaffold ready.

**Local remediation:** scaffold plans now map a redundant text `name` field
to the built-in title when its seed values match the title, label that title
control Name, and keep a truly distinct Name field when values differ. The
approved-design verifier accepts this alias. A staged entry with an explicit
new Track reference no longer inherits the browser's unrelated focused view.
Browser smoke in Name Label Check showed one Name control and one Name column.

### P2: Sample record contains values the user did not provide

The user requested one sample item named `Demo Generator` but did not provide a
date or location. The scaffold filled `Purchase Date` as `2026-01-15` and
`Location` as `Warehouse` without marking them as examples. These are invented
facts in a register-like app.

**System improvement**: keep unspecified sample attributes blank, or mark
proposed values as examples and request confirmation when they could be
mistaken for real records.

**Skill improvement**: separate user-provided values from illustrative
defaults and disclose any generated values in the review proposal.

**Local remediation:** scaffold defaults no longer fill missing seed fields
with example dates, locations, statuses, or other synthetic values. Plan
fidelity now compares every seed field value with the approved blueprint,
and the scaffold skill directs the resident to preview exact values and leave
unspecified fields blank. Browser smoke in Blank Sample Check showed Demo
Generator with blank Location and Purchase Date cells.

### P2: Admin password setup guidance is inconsistent

On the first startup, the configured admin bootstrap was rejected because the
runtime requires at least 12 characters while `.env.example` says at least 6.
The server continued starting, so the UI appeared available without the
advertised admin account. The user updated the `.env` password and requested a
fresh database; after resetting the local Postgres volume and restarting,
browser login succeeded.

**System improvements**

1. Align `.env.example`, setup prompts, and runtime validation with the same
   password policy.
2. Clearly surface failed admin bootstrap rather than presenting setup as
   ready without an account.
3. Show password requirements beside the signup password field.

**Skill improvement**: verify bootstrap readiness and successful browser login
before starting authenticated smoke flows; never report setup complete based
on a running process alone.

**Local remediation:** Core configuration, `.env.example`, generated setup
guidance, bootstrap validation, signup, and reset now use the framework's
12-character minimum. An invalid configured admin password or a failed
bootstrap aborts server startup with a clear error. Browser smoke confirmed
signup and reset guidance. Focused checks confirmed short configured passwords
fail and graph-catalog or personal-workspace setup errors propagate. The live
startup failure path was not exercised against the working local database.

### P2: Email verification notice persists after successful login

The authenticated dashboard and track pages kept showing “Verify your email”
with resend and code-entry actions. Login and product work remained available,
which matches the documented non-blocking verification behavior, but the
persistent notice does not explain whether verification is required for any
specific action.

**System improvement**: explain what verification enables and whether the user
can keep working without it; avoid repeatedly interrupting unrelated tasks.

**Local remediation:** the notice now says that verification confirms address
ownership and that work can continue. Not now defers the banner for that user
in the current browser session. Browser smoke showed the copy, then confirmed
the banner stayed hidden after Not now and a page reload.

## Positive browser evidence

- Signup explains that a personal workspace is created automatically and a
  collaborative workspace is optional.
- The Apps empty state explains the concept and offers “Ask Integral to
  scaffold an app” and “Explain this app structure” actions.
- The resident agent proposed an App, Track, fields, views, and sample item in
  plain language and created the basic app structure after confirmation.
- Filing a second synthetic item through the visible form succeeded.
- Editing an item's status through its entry editor succeeded and the table
  reflected the new value.
- Manual board settings were discoverable and could align the board to the
  requested two statuses.

## Evidence limits

- All product-flow checks used the visible local browser at
  `http://127.0.0.1:9006`; no API calls or automated suites substituted for
  browser evidence of user behavior.
- The fresh database contains the synthetic Equipment Register app and the
  synthetic Status Alignment Check app used for remediation smoke checks.
- Search follow-up covered three records; larger data sets and other view
  modes remain outside this smoke pass.
- Findings describe the work branch based on `main` and the local environment,
  not a production deployment.
