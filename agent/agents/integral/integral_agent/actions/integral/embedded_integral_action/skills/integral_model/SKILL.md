---

name: integral_model
description: Coaches domain modeling — shapes entry types, fields, and reference patterns (lookup vs expansion/anchor) by reading the profile, proposing schema changes, wiring relations, and saving views. Advises integral_scaffold during greenfield delivery without taking over its design/build lifecycle; delegates record edits to integral_entries.
spec: jv
allowed-tools:
  - integral_describe_substrate
  - integral_describe_model
  - integral_get_track_schema
  - integral_list_apps
  - integral_list_tracks
  - integral_modify_model
  - integral_get_model_draft
  - integral_propose_model_revision
  - integral_diff_model_draft
  - integral_publish_model_draft
  - integral_link_entries
  - integral_save_view
requires-actions:
  - EmbeddedIntegralAction
extends: action:integral/embedded_integral_action
tags:
  - integral
  - modeling
  - schema
  - profiles
  - relations
---

# Integral model — SOP

Greenfield composition (field/view palettes, weave contracts, complete-app
assembly) lives in skill **integral_scaffold**. This skill owns lookup vs
anchor edge cases and existing-schema revisions when advising scaffold.

## Purpose / when to use

The user is asking **how to structure** their information, or wants to change the
shape of what they already have. Their words sound like:

- "How should I model projects and their tasks?"
- "Add a 'client' field to Deals that points at a Contact."
- "I want each Project to have its own tasks/activities/notes."
- "Should contacts and companies be separate tables or one?"
- "Add a status field and a board view to this track."

The deliverable is **better structure**: the right entry types and fields on a
track's Operational Model, relations wired with the correct pattern, and a view
that projects the resulting shape so the user can see it work.

This skill carries the **modeling judgment** — it knows the Track↔table /
Entry↔record analogy and the two reference patterns. It coordinates the profile
and relation tools; it does not file content or stand up whole new apps.

## When NOT to use this → delegate

- **Standing up a whole new app/domain** from a one-line intent → **`integral_scaffold`**
  (which calls this skill's primitives for a starter shape). For a new app, provide modeling decisions back to scaffold; do not
  restart discovery or require a separate design approval.
- **Creating / editing the actual records** (not their schema) → **`integral_entries`**.
- **Filing freeform content** into existing structure → **`integral_filing`**.
- **Bulk reorganizing existing entries** (move, re-tag, archive) → **`integral_organize`**.
- **Pure profile lifecycle ops with no modeling decision** (discard a draft,
  list packages) → **`integral_models`**.

If the user wants records changed rather than the table's shape, you are in
`integral_entries`, not here.

## Grounding — read before you propose

You **never** modify a profile you have not inspected this turn. Modeling
decisions flow from introspection, not from memory or domain stereotypes.

**Resolve every id from a tool result — never construct or guess one.** App and
track ids are opaque (`n.WorkspaceApp.<hash>`, `n.Track.<hash>`); you cannot
derive them from a name. When the user names an app or track ("the CRM app", "the
Stock track"), your FIRST call resolves that name to a real id:

- **`integral_list_apps`** → match the app by name, take its `id` verbatim.
- **`integral_list_tracks(app_id=<that id>)`** → match the track by name, take its
  `id` verbatim.

Never pass an `app_id`/`track_id` you did not read out of a tool result this turn.
A fabricated id silently resolves to nothing — `integral_list_tracks` returns
`{total: 0, tracks: []}` (or `app_id_unresolved: true`), which is **not** "the app
is empty", it is "that id is wrong". If you see `app_id_unresolved` or an empty
result you did not expect, STOP and call `integral_list_apps` to get the real id;
do not conclude the app has no tracks.

Then inspect:

1. **`integral_describe_substrate`** — the global palette: every field type
   (`text`, `number`, `date`, `select`, `multi_select`, `relation`, `computed`,
   …) and view-palette key the substrate can actually render. Propose only from
   this set.
2. **`integral_describe_model`** (or **`integral_get_track_schema`**) — the
   profile **currently attached** to the track/app: its existing entry types,
   fields, tags, views, and any pending draft. This tells you what is already
   there so you propose a *delta*, not a duplicate.
3. **`integral_list_tracks`** — when a relation will point at another table,
   resolve that target track's id and read its schema too (a `relation` field's
   `target: track` anchors a real, existing track).

## The modeling decision — two reference patterns

This is the core judgment the skill exists to apply. When a record needs to point
beyond its own fields, choose **exactly one** pattern, never both for the same
relationship, never invent a third:

| Pattern | Field shape | Edge | Use when |
|---------|-------------|------|----------|
| **Lookup / value reference** | `relation` field, `target: entry` | `REFERENCES` | The target is itself a first-class record and many records point at one (Project → its client Contact). |
| **Expansion reference (anchor)** | `relation` field, `target: track` | `ANCHORS` | One parent record legitimately owns a heavyweight, mixed-entity detail collection with its own views/ACLs (Project → a Project-Details track of tasks, activities, updates). |

**Depth via mixed entity types, not one track per category.** When a parent owns
several kinds of child (tasks *and* activities *and* updates), declare **multiple
`EntryType`s under one anchored track** and let each view project a slice via
`entry_type_keys` — do **not** provision one anchored track per category.

**Negative space — never:**
- Stuff a child collection into a parent entry's JSON/custom-field payload (the
  children lose first-class status: no comments, no ACLs, no agent visibility).
- Invent a hierarchical containment edge — anchored tracks stay top-level under
  the app; the anchor is an additional pointer, not containment.
- Use both a lookup and an anchor for the same relationship.

## Procedure

For a **single discrete** schema change (add one entry type / view / tag):

1. Ground (substrate + current profile, above).
2. **`integral_modify_model`** — `action=add_entry_type | add_view | add_tag | remove_*`, with `track_id` **or** `app_id` (not both).
   Propose only types confirmed by the substrate.
3. Optionally **`integral_save_view`** so the new shape is visible.

For a **multi-step** schema change (several fields, a relation, a new view
together) — use the **draft lifecycle** so the whole revision stages as one card:

1. Ground.
2. **`integral_get_model_draft`** — use its current schema and returned draft identifier; never invent one.
3. **`integral_propose_model_revision(draft_id, operations=[…])`** — batch the
   patch-DSL ops (`add_entry_type`, `add_field`, `add_view`, `add_relation`, …) in
   one call. Choose the relation `target` (entry vs track) per the table above.
   - **Every `add_field` op MUST carry `entry_type` (the EntryType key the field
     lands on) and a `spec` with at least `key` and `type`.** Get the EntryType key
     from `integral_get_track_schema`/`integral_describe_model` first — never
     leave it blank (a missing `entry_type`/`key` stages as "field ? on entry type
     undefined" and fails).
   - **A relation field's config is NESTED under `spec.relation` — flat keys on the
     spec are silently ignored.** Put `target`, `target_track_types`,
     `target_entry_types`, `allow_cross_track`, `many` inside a `relation` object,
     not at the top of `spec`. A relation whose target lives in a **different
     track** (the normal lookup case — e.g. Deal → Contact) is **cross-track**, so
     `relation.allow_cross_track` MUST be `true`; otherwise `integral_link_entries`
     is rejected at apply time with "cannot reference entries across tracks" and the
     link silently fails to materialize. Name the target track(s) in
     `target_track_types` and the allowed target type(s) in `target_entry_types`.
     Concrete shapes:
     ```
     # lookup → another record in a DIFFERENT track (REFERENCES). Cross-track →
     # allow_cross_track: true is REQUIRED, else the link is rejected:
     {op: "add_field", entry_type: "deal",
      spec: {key: "primary_contact", name: "Primary Contact", type: "relation",
             relation: {target: "entry", target_track_types: ["contacts"],
                        target_entry_types: ["contact"], allow_cross_track: true,
                        many: false}}}
     # expansion → a companion track (ANCHORS):
     {op: "add_field", entry_type: "project",
      spec: {key: "details", name: "Details", type: "relation",
             relation: {target: "track", target_track_template: "project_details",
                        auto_provision: true}}}
     ```
4. **`integral_diff_model_draft(draft_id)`** — read the structural diff **and**
   the per-track count of entries the change would touch. Explain the impact to
   the user before committing.
5. **`integral_publish_model_draft(draft_id)`** — the commit step; stages the
   publish for bless.

To **wire a relation on actual records** once the field exists:

- **`integral_link_entries(source_entry_id, field_key, target_id)`** — sets the
  relation field, materializing `REFERENCES` (entry target) or `ANCHORS` (track
  target) with the `field_key`. The relation field must already exist on the
  source entry type (add it via the profile first). **A scalar id written into a
  plain field is never a substitute for this edge.**

## Staging discipline

- `integral_modify_model`, `integral_propose_model_revision`,
  `integral_publish_model_draft`, `integral_link_entries`, and
  `integral_save_view` are all **propose** tools — each stages a change the user
  blesses; there is no separate execute step.
- For multi-step modeling, run the draft lifecycle (or open a batch) so the user
  blesses **one** coherent revision, not a flurry of micro-cards.
- Present the **diff** in plain terms ("this adds a `client` relation on Deals → 14
  existing deals get an empty `client`") and **wait**. Do not say a field/relation
  "exists" until the staging-resolved marker fires.
- Surface any error envelope verbatim; never retry a failed revision blindly.

## Forbidden patterns

- Modifying a profile you did **not** read this turn with
  `integral_describe_model` / `integral_get_track_schema`.
- Proposing a field type or view type **not** in `integral_describe_substrate`.
- Using **both** a lookup and an anchor for the same relationship, or inventing a
  third reference pattern.
- Stuffing a child collection into a parent entry's JSON payload instead of an
  anchored track + entry types.
- Provisioning **one anchored track per child category** when mixed entry types +
  per-view `entry_type_keys` filtering would do.
- Writing a scalar foreign-key id into a plain field and calling it a relation —
  use `integral_link_entries` so the `REFERENCES`/`ANCHORS` edge is materialized.
- Publishing a draft without showing the user the `integral_diff_model_draft`
  impact first.

## Example walkthrough

> **User:** "Each Project should have its own tasks, activities, and notes."

1. `integral_describe_substrate` → `relation` (target track) and `kanban`/`feed`
   views are supported.
2. `integral_describe_model(track_id=<projects>)` → Projects has no detail
   relation yet.
3. **Decision:** one parent owns a *mixed-entity heavyweight* collection →
   **expansion / anchor** pattern, with **multiple entry types** in the detail
   track (not three separate tracks).
4. `integral_get_model_draft(<projects profile id>)` → `draft_id`.
5. `integral_propose_model_revision(draft_id, operations=[
     {op:add_relation, field_key:"details", target:"track", …},
     // detail track profile: entry types task / activity / note
   ])`.
6. `integral_diff_model_draft(draft_id)` → explain: "Adds a `details` anchor on
   Projects to a Project-Details track holding task/activity/note records."
7. `integral_publish_model_draft(draft_id)` → stage for bless. **Wait.**
8. After bless, `integral_save_view` on the detail track: a `kanban` of `task`
   entries by status, a `feed` of `activity`+`note`. To attach a specific
   project's detail track, `integral_link_entries(source=<project entry>,
   field_key="details", target_id=<detail track>)`.

> **User:** "Add a client to each Deal that points at a Contact."

Target is a first-class record, many deals → one contact → **lookup** pattern:
add a `relation` field `client` with `target: entry` on the Deal entry type
(`integral_modify_model` / revision), then `integral_link_entries` per deal —
materializing `REFERENCES`, never a bare id field.
