---
name: integral_scaffold
description: "Owns operational app delivery from a business need: guide design, batch the approved schema, relations, views, operating skills and reminders, then verify the applied result. Use for new apps and continuing or repairing their builds; retain ownership while consulting modeling and scheduling skills."
spec: jv
# Prefer-heavy is documented intent for harnesses that honor it. Integral's
# agent.yaml sets planning_heavy_first_tick: true so tick 0 is already heavy —
# light gear must not own greenfield scaffold (it skips use_skill and replies).
# First tool this skill expects after activation: continue the propose/build SOP
# (never a free-form "approve in Integral" prose bypass).
allowed-tools:
  - integral_whoami
  - integral_describe_substrate
  - integral_list_models
  - integral_list_apps
  - integral_ask_user
  - integral_propose_design
  - integral_upsert_artifact
  - integral_get_artifact
  - integral_list_artifacts
  - integral_begin_batch
  - integral_build_approved_design
  - integral_create_app
  - integral_create_app_track
  - integral_apply_model_to_track
  - integral_author_model
  - integral_save_view
  - integral_create_entry
  - integral_create_dashboard
  - integral_author_skill
  - integral_commit_batch
  - integral_list_tracks
  - integral_get_track_schema
  - integral_query_entries
  - integral_list_views
  - integral_schedule_task
  - integral_list_routines
  - integral_cancel_batch
requires-actions:
  - EmbeddedIntegralAction
extends: action:integral/embedded_integral_action
tags:
  - integral
  - scaffold
  - setup
  - apps
  - batch
---

# Operational app delivery

## Delivery ownership and status

This skill coordinates a new app or an addition to an existing app. Follow this sequence without
skipping or repeating a settled phase: **discover → clarify → propose →
preview → authorize → execute → verify → explain**.

Specialists advise without taking over this lifecycle: `integral_model` owns
existing-schema judgment, `integral_models` owns model/package lifecycle,
`integral_entries` owns records, and `integral_scheduling` owns cadence.

Use state words precisely: **proposed** is not authorized; **prepared** or
**awaiting approval** is not applied; **applied** is not verified. Say “built”
only after a receipt reports the batch applied. Say “verified” only after the
readback succeeds. A failed, rejected, cancelled, or partial receipt must be
named as such and must never be rendered as a saved result.

### Explicit design-only boundary

When the user says **“design only,” “do not build,” “do not create,”** or gives
an equivalent instruction, this turn is proposal-only. Call the grounding and
`integral_propose_design` tools, then stop. Do **not** call
`integral_begin_batch`, `integral_create_app`, `integral_create_app_track`,
`integral_apply_model_to_track`, `integral_save_view`,
`integral_create_entry`, `integral_author_skill`, `integral_schedule_task`, or
`integral_commit_batch` in that turn.

In the user-facing reply, begin with **“Proposed — nothing has been built.”**
Paste the full proposal, then end with this exact sentence on its own line:
**Confirm this design, or tell me what to change.** A design proposal, a stored blueprint, a staged receipt, and an
applied app are distinct states; never describe one as another.

## When to use

User wants a new operational app, an addition to one, or to finish / repair one. Own
**discover → design → build → verify → handoff**. Chat affirm of the design
outline is greenfield approval; the scaffold batch applies on
`integral_commit_batch` (no second Prompt Sheet bless). An App node alone is
not a complete app — schema, views, relations, procedures, and acceptance
evidence must land.

Propose from the field and view types in this skill. Call
`integral_describe_substrate` only when a tool rejects a type or config key.
Do not spend a turn on whoami, model listing, or substrate introspection
for a clear new-app or existing-app request.

**Do not narrate a shadow workflow.** For an explicit request to create an
app, activate this skill and call the proposal tool before replying. A prose
outline with no `integral_propose_design` record is not a design step. Once a
recorded proposal is affirmed with "go ahead", "build it", or equivalent,
begin and commit the build in that same turn. Do not reproduce a long design,
ask for the same confirmation again, or imply that a further approval is
needed. A reminder is only part of the delivered app when its routine is in
the batch with a stated cadence and timezone; otherwise call it a proposed
follow-up, never an automated reminder.

## When NOT to use — delegate

Existing-record CRUD → skill `integral_entries`. Existing-schema changes →
skill `integral_model`. Library lifecycle → skill `integral_models`.
Routine-only work → skill `integral_scheduling`. For greenfield, consult those
as specialists but **retain delivery ownership**. Do not stop at an empty
skeleton or bounce the user between skills mid-build.

## Substrate constituents

Canonical mental model: **App ≈ schema / database**, **Track ≈ table**,
**Entry ≈ record**. Everything below is what you compose into a complete app.

| Constituent | Role |
|-------------|------|
| **App** | Workspace-scoped container; groups tracks; may host app-scoped skills. |
| **Track** | Typed table under an app; owns an attached Operational Model. |
| **EntryType** | Record shape under the track profile (`key`, `name`, `fields[]`). One track may declare multiple entry types; views can slice via `entry_type_keys`. |
| **Field** | Column on an entry type. Built-ins below; live set from `integral_describe_substrate`. |
| **Relation** | Field `type: relation` — the only first-class cross-record pointer. |
| **View** | Projection of a track (or app surface) via a palette `view_type` + `config`. |
| **Operational Model** | Schema document attached to app/track (entry types, taxonomy, views). Inline on `create_app_track`, apply a library package, or author/modify. |
| **Library package** | Reusable profile template. Track-scope packages shape a track; app-scope packages are not per-track templates — never apply an app package to every track. |
| **Entry** | Concrete record. Demo seeds use structured `fields` + `entry_type`. |
| **App skill** | Authored SOP (`integral_author_skill`) for multi-step operating procedures. Agent-guided behavior, not a DB constraint. |
| **Routine** | Scheduled reminder (`integral_schedule_task`) — personal cadence in chat; date fields do not notify by themselves. |
| **Batch** | Single approval unit: begin → append ops → commit. Greenfield chat-affirm applies on commit. |
| **Artifact** | Session notes (e.g. `app_design_blueprint` from `integral_propose_design`). |

### Field palette

| `type` | Use |
|--------|-----|
| `text`, `markdown` | Free-form / long form |
| `number`, `boolean` | Scalar |
| `date`, `datetime` | Time placement (calendar / timeline / reminders) |
| `select`, `multi_select` | Closed option sets (`enum` / options) — boards group on these |
| `relation` | Lookup or anchor (see Weave) — config **nested** under `spec.relation` |
| `computed` | Derived values the substrate supports |
| `file`, `files` | Attachments — gallery image source |
| `json` | Structured blob when no typed field fits |
| `member` | Workspace member reference |

Always confirm advanced shapes and required config keys via
`integral_describe_substrate` — do not invent field types.

### View palette

Profiles reference **palette keys**, not UI code. Prefer the smallest view that
answers an operational decision. Every non-baseline view needs supporting
fields already on the track.

#### Core widgets

| `view_type` | What it is | Weave contract |
|-------------|------------|----------------|
| `table` | Sortable/filterable grid; `config.columns` optional | Default working surface. **Every track should get one.** |
| `feed` | Chronological stream (`default_always_on`) | Activity / update-shaped tracks. Do not add for “completeness” on every table. |
| `kanban` | Column board; `group_by` and/or `kanban_columns` | Needs a `select` (or equivalent discrete field) whose values are columns. |
| `calendar` | Month/week/day; `calendar_mapping: { date_field, end_date_field? }` | Needs `date` / `datetime` fields. |
| `gallery` | Card grid with image preview | Needs `file`/`files` (or image URL field); else do not promise gallery. |
| `wiki` | Hierarchical pages (UI label **Wiki**); `parent_field`, `body_field`, `title_field` | Needs parent `relation` → entry + markdown body. |

#### Composable meta-widgets

Declarative grouping/sort/filter/projection — use when a core widget’s fixed UX
is not enough, without inventing new React.

| `view_type` | Role | Key config |
|-------------|------|------------|
| `composable_list` | Generic list | `group_by`, `sort`, `filter`, `projection`, `density` |
| `composable_grid` | Card grid | `group_by`, `color_by`, `projection`, `card_layout` |
| `composable_board` | Board via config (swimlanes, color) | `group_by`, `color_by`, `swimlanes`, `sort_within_column` |
| `composable_timeline` | Vertical time axis | `date_field`, `end_date_field?`, `group_by`, `color_by` |

#### Other palette entries

| `view_type` | Notes |
|-------------|--------|
| `extension_view` | Sandboxed app-package view (`extension_view_key`) — only when an installed extension exposes one. |
| Manifest `view_types[]` composites | Profile-local aliases over a base palette key (e.g. a named board). Not new palette entries; resolve via profile tooling. |

Dashboard / region contracts (`summary_tiles`, chart regions, layout containers,
…) exist in the contract catalog for richer surfaces — only use when
`integral_describe_substrate` (or profile tooling) lists them as creatable for
your path. Prefer core + composable for first apps.

**View selection rule:** name the decision the user must make, pick one view
type that answers it, ensure required fields exist, then stop. Do not sprinkle
feed/gallery/kanban on every track.

## Weave patterns — how constituents form a complete app

1. **Tables first.** Map managed nouns → tracks; attributes → fields; closed
   operational states → `select` / `multi_select`. Prefer a small coherent set
   of tracks over a sprawling schema.
2. **Entity vs attribute.** Own track only when the thing has several fields,
   its own list/views, or is referenced from **two or more** other tracks.
   Otherwise keep a scalar/text field on the parent record.
3. **One source of truth.** Do not mirror the same fact in two fields. Pick one
   authoritative representation; procedures and views read that.
4. **Two reference patterns — pick one per relationship** (skill
   `integral_model` for edge cases):
   - **Lookup** — `relation` with `target: entry` → `REFERENCES`. Many records
     point at one independently managed record. Put the relation on the side
     that *points*. Cross-track lookups are allowed and are the normal case.
     Set `allow_cross_track: true` and `target_track_types`. Never tell the
     user that relation fields must stay inside one track.
     Config nested under `relation`:
     ```json
     {"key":"…","name":"…","type":"relation","relation":{
       "target":"entry","target_track_types":["…"],
       "target_entry_types":["…"],"allow_cross_track":true,"many":false}}
     ```
   - **Anchor** — `relation` with `target: track` → `ANCHORS`. Parent owns a
     heavyweight detail collection with its own views/ACLs. Prefer **multiple
     EntryTypes under one anchored track** + `entry_type_keys` on views over
     one anchored track per child category.
   - Never both for the same relationship. Never invent reverse “list of X”
     relation fields on the looked-up side — reverse browse is a view/query.
5. **Views bind to fields.** Board ↔ select; calendar/timeline ↔ date(s);
   gallery ↔ file/image; wiki ↔ parent relation + markdown; table ↔ always.
6. **Procedures close the loop.** Multi-record consistency that users expect
   (“doing A also updates B”) is an `integral_author_skill` in the same batch —
   prose in the design is not acceptance. Skills guide; they are not locks.
7. **Time → routines.** Expiry / due / service dates that must surface later
   need `integral_schedule_task` (timezone + cadence). A date field alone does
   not notify.
8. **Seeds prove the graph.** Demo entries (unless user wants empty) should
   exercise each track and each lookup edge with fictional labels — no real PII.
9. **Honesty.** Say what the substrate cannot enforce (concurrency locks,
   automatic side effects without a skill/routine). Never silently downgrade a
   requirement.
10. **Acceptance is inspectable.** Checklist lines map to concrete fields,
    views, relations, skills, routines, or demo rows you will create.

## Grounding (read before write)

1. `integral_list_apps` when the request may extend an existing App. Continue
   a partial build via `integral_list_tracks` rather than duplicating.
2. `integral_describe_substrate` only after a type or config key is rejected.
3. `integral_list_routines` when scheduling — avoid duplicates; establish IANA
   timezone (ask if unknown).

Batch tokens for new objects: `{{app.id}}`, `{{track.id:<Name>}}`,
`{{entry.id:<Label>}}`. A token only references objects created **earlier in
the same batch**. Never fabricate ids. Unique names within the build.

## Procedure

### 1. Guide the design

Translate need → tracks, fields, relations, views, procedures, reminders using
the weave patterns above. Ask only questions that change the operational
result (`integral_ask_user` for real forks). Offer defaults; distinguish manual
status, agent-guided skills, and enforced rules.

**Respect resolved scope.** When the user says an app must be *distinct*,
*separate*, or *new*, that is an explicit decision to create a new App with
the requested name even if similarly shaped apps already exist. Mention the
nearby apps only when they create a concrete naming conflict; do not reopen
the reuse-versus-create question after the user has affirmed the design.

Call `integral_propose_design` with full design in `proposal`. When adding a
Track to an existing App, include its real `target_app_id` from
`integral_list_apps`; this binds the approved design to that App:
- App + each track (purpose, entry type(s), fields, lookups/anchors)
- Views with supporting field keys and the decision each answers
- Operating procedures to author as skills
- Reminders (dates, lead window, cadence, timezone, delivery in this chat)
- Demo plan or explicit empty
- Short inspectable acceptance checklist

**Preview the same proposal markdown in your reply** — the user reads chat,
not an internal artifact. The tool stores the revision as
`app_design_blueprint` (`integral_get_artifact`). End turn; wait for confirm
or correct. This preview is not authorization and creates nothing. For an
explicit design-only request, begin the reply
“Proposed — nothing has been built.” and end with “Confirm this design, or
tell me what to change.”; do not call a build tool. Correction → `integral_propose_design`
again from prior body + deltas only. Affirm with no shape change → build (no
re-propose and no second approval).

### 2. Build the confirmed design

Chat affirm ("looks good", "proceed", "build it") **is** approval. Finish in
the same turn. `integral_build_approved_design` applies the affirmed design
immediately for new Apps and approved existing-App additions. Do not request
a second approval or promise a Prompt Sheet on this path.

Use one `integral_build_approved_design` call with the complete ordered
`operations` array. Each item is `{tool: "integral_…", args: {...}}`. The only
valid `tool` values inside that array are `integral_create_app`,
`integral_create_app_track`, `integral_save_view`, `integral_create_entry`,
`integral_create_dashboard`, `integral_author_skill`, and
`integral_schedule_task`. Never put `integral_author_model` inside this array:
it creates a detached library model, not an App Track. Put each Track's fields
inside `integral_create_app_track.args.entry_types`.

For a **new App**, first use `integral_create_app`, then create its Tracks with
`app_id: "{{app.id}}"`. For an **addition to an existing App**, look up its
real ID, pass `target_app_id`, and make the first operation
`integral_create_app_track` with that same real `app_id`. Never create the App
again. Then add the requested views, records, dashboard, skills and routines.
App and Track names must match the approved proposal. Use the published
argument names `name` and `description` on track creation and `name`,
`view_type`, `track_id`, `config` on view creation. Keep the view name, type,
and track reference at the top level of `integral_save_view.args`, not inside
`config`. A Wiki view uses
`config.parent_field` with the key of a `relation` field on the new Track;
its Body field should be `markdown`. For a parent-page relation use
`relation: {target: "entry", target_entry_types: ["Wiki Page"],
allow_cross_track: false, many: false}`. Do not use `relation.track`,
`relation.entry_type`, or `hierarchy_field`. Plan only the views the approved design names. Do not add an
"All {Track}" table or a calendar unless that design asked for one. The
platform Feed is already on every Track. If the approved
design says no demo entries, add none; the builder honors that choice.
After `applied: true`, finish the same turn with a plain readback naming the
App, Track, fields, Wiki view mapping, and whether any demo entries exist.
Do not ask for another approval and do not end on the system marker alone.
Copy the approved App and Track names exactly; do not rename the App at build
time. This operation commits its own batch: do not put `integral_begin_batch`
or `integral_commit_batch` inside the operations array, and do not stop after
an error if the same approved design can be corrected. Include the requested
dashboard in this same plan. For dynamic date filters, use typed values
`{"relative_date_days": 0}` (today) or `{"relative_date_days": 7}` (seven
days ahead); saved-view filters use `config.filters` entries with `field`,
`operator`, and `value`, while dashboard `data_source.filters` uses `field`,
`op`, and `value`.
If it returns `invalid_scaffold_plan` or `plan_differs_from_design`, revise
the `operations` array and call **the same tool again in this turn**. These
preflight errors have made no writes and need no second user approval. Do not
switch to `integral_begin_batch` or author detached library models to work
around a rejected fresh plan. If the tool reports a partial apply, inspect
its receipt and repair only the unfinished portion of that existing App.

The operation uses the same policy-bound staging and batch executor as the
individual tools, commits once, and returns an applied receipt. It fills
omitted baseline tables and date calendars from declared fields, plus
synthetic demo records unless the approved design explicitly excludes them.
Use the individual calls below only when resuming an already
open batch that contains writes; never submit the same new App through both
paths. Do not open a manual batch for a freshly approved design.

1. `integral_begin_batch` once (re-enter keeps prior ops).
2. `integral_create_app` (or extend existing by real id).
3. `integral_create_app_track` for every planned track with inline
   `entry_types`/fields. A detached library model is not an attached schema.
4. `integral_save_view` per track — table baseline; additional views only with
   real field keys and valid config for that `view_type`. A table must include
   `config.columns` using `custom_fields.<field_key>`; a kanban must include
   `group_by: custom_fields.<select_field>` and `kanban_columns`; a calendar
   must include `calendar_mapping.dateField`. An empty config produces a
   generic platform view and does not complete a scaffold.
5. `integral_create_entry` demos unless empty requested — `entry_type` +
   structured `fields`; referenced records before dependents. Never put
   `Field: value` lines only in `text`: that supplies a title but leaves every
   operational field empty. For a linked record use a named batch reference,
   e.g. `fields: {vehicle: "{{entry.id:Honda Civic}}", status: "Active"}`
   after the Honda Civic entry. The approved-plan builder converts exact
   labelled seed text when possible and rejects ambiguous lines.
6. `integral_author_skill` for agreed multi-step procedures (`app_id`,
   discovery description, `tools_required`, `body_override`; seven SOP
   sections). Private app scope by default.
7. `integral_schedule_task` in the same batch after referenced tracks exist
   (skill `integral_scheduling`). Cron + IANA timezone; self-contained
   instruction with batch tokens; read-only reminders stay free of write_scope.
8. Checklist vs ops, then `integral_commit_batch`. Explicitly empty app only:
   `allow_empty=true`; schema and views remain mandatory.

Dependency order inside the batch: app → tracks/schemas → views → seed
entries (parents before linked children) → skills → routines → commit.

### 3. Recover without duplication

- `design_amend_required` — re-propose prior+deltas; do not build stale outline.
- `affirm_build_instead` / `already_proposed` — build, do not re-propose.
- `batch_incomplete` / `ready:false` — append missing ops to **same** batch,
  commit again.
- Invalid args — fix from error + live contract; do not repeat identical fails.
- Bad refs/order in open batch — `integral_cancel_batch`, rebuild in order.
- Partial execution — resume retryable batch; do not recreate completed objects.
- User rejection — stop.

### 4. Verify and hand off

After `applied` / `execute_result` (or `[SYSTEM:STAGING-RESOLVED] … consumed`
for Prompt Sheet writes), read back: `integral_list_apps`,
`integral_list_tracks`, `integral_get_track_schema`, `integral_list_views`,
`integral_query_entries`, `integral_list_routines`. Use returned ids.

Confirm tracks, fields/options, views, seeded relations, skills, and routine
timezone/next run. Repair gaps with a scoped batch. Do not claim a routine has
fired merely because it is active.

Handoff: app link, brief how-to-operate, reminder cadence, plain limitations.
"Built" requires readback; no commit token means nothing applied.

## Staging discipline

Design confirmation in chat; greenfield apply on `integral_commit_batch` after
affirm. Propose tools only accumulate while a batch is open. Wait for apply /
consumption before claiming creation. Never execute around the approval path.
Sequential batches are resumable, not atomic transactions.

## Forbidden patterns

- Skeleton app (no field-bearing schemas / no views) called “done”.
- Field or view types not in live substrate; guessed config keys.
- Views without their weave-contract fields.
- Reverse-list relation fields; dual lookup+anchor for one relationship;
  duplicate state fields.
- Forward batch token refs; fabricated ids; duplicate apps/tracks on recovery.
- Procedures described in prose with no `author_skill` / batch step.
- Date fields treated as notifications; reminders without timezone/cadence.
- Prompt Sheet language for chat-affirmed greenfield.
- Realistic PII in demos; demos when user asked for empty; silent destructive
  automation.
- Scaffold ↔ model ownership ping-pong; one card per create instead of one
  batch; repeating identical failing tool calls.

## Example — abstract weave

User asks for an operational app. Propose directly from the types in this skill:

**Propose (chat):** App with tracks **A** (assets/items), **B** (parties),
**C** (events/transactions). C holds lookups → A and → B (one-sided,
`allow_cross_track: true`). A has a `select` for operational state and
`date`/`datetime` fields for due/expiry where needed. Views: table on each
track; kanban or `composable_board` on A only if the select exists; calendar
or `composable_timeline` on C only if date fields exist; no gallery without
`file`/`files`. Name skills that keep A and C consistent; name any routine
that watches date fields. Demo seeds exercise A, B, then linked C. Checklist
maps 1:1 to those objects.

**On affirm:** one batch — create app → create/shape tracks → save views →
seed entries → author skills → schedule routines → commit. Verify via list/
schema/query tools. Hand off with link and operating notes.

Leave domain naming, track count, and which palette keys fit to judgment
guided by the user’s need and the weave contracts above.
