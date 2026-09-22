---


name: integral_organize
description: "Bulk-reorganizes, migrates, or archives existing entries — selects a set with a query, then applies one batched change so the user blesses the whole reorg once. Use for cross-entry status moves, archival sweeps, and tag migrations. Delegates single-entry edits to integral_entries and schema changes to integral_model."
spec: jv
allowed-tools:
  - integral_list_tracks
  - integral_get_track_schema
  - integral_query_entries
  - integral_query
  - integral_count_entries
  - integral_create_tag
  - integral_begin_batch
  - integral_bulk_update_entries
  - integral_add_entry_tag
  - integral_remove_entry_tag
  - integral_bulk_delete_entries
  - integral_delete_entry
  - integral_update_entry
  - integral_commit_batch
  - integral_cancel_batch
requires-actions:
  - EmbeddedIntegralAction
extends: action:integral/embedded_integral_action
tags:
  - integral
  - organize
  - bulk
  - migrate
  - archive
  - batch

---

# Integral organize — SOP

## Purpose / when to use

The user wants to change **many entries at once** along some dimension — a bulk
re-status, re-tag, field patch, move, or archive/delete. Their words sound like:

- "Move all Q3 deals to Q4."
- "Archive everything older than six months and tag it `legacy`."
- "Mark all these tasks done."
- "Re-tag every entry in this track from `draft` to `published`."
- "Delete all the test records."

The deliverable is a **set selection → one batched change → one bless**. The shape
is always: *narrow the set with a read, then fan the same change across it under a
single approval card.*

## When NOT to use this → delegate

- **Editing one specific entry** (title, body, a field) → **`integral_entries`**.
  Reach for organize only when the change spans **many** entries.
- **Changing the schema** (adding the field/status/tag *type* you want to set) →
  **`integral_model`**. Organize sets values on existing fields/tags; it does not
  invent the field or the tag's place in the profile.
- **Filing new freeform content** → **`integral_filing`**.
- **Standing up a new area** → **`integral_scaffold`**.
- **Read-only synthesis / digests / saved review views** → **`integral_review`**.

If the target tag or status value does not yet exist on the profile, hand the
schema part to `integral_model` first, then come back to apply it in bulk.

## Grounding — select before you mutate

A bulk change is only as safe as the set you select. **Always** read the set
first, and confirm its size, before staging anything:

1. **`integral_list_tracks`** — resolve the track(s) in play.
2. **`integral_get_track_schema`** — read the valid status values, field keys, tag
   names, and entry types for the track. You set values **by these keys** — never
   by guessing a status string or tag name.
3. **Select the set** with the right read tool:
   - **`integral_query_entries`** — structured filter (track, status/statuses,
     tags, entry_type, exact custom `filters`, `since`/`until`, sort). For a
     model field, fetch its exact key with `integral_get_track_schema`, then use
     a filter such as `{ "custom_fields.priority": "High" }`. The workhorse for "all Q3 items",
     "everything older than 6mo". **Raise `limit`** so you cover the *whole* set,
     not the first page — a bulk op must act on every matching entry.
   - **`integral_query`** — only when the selection is concept/meaning-based and
     no structured filter expresses it.
4. **`integral_count_entries`** — confirm the magnitude ("this matches 47
   entries") before you stage. Tell the user the count; a surprising number means
   re-check the filter, not stage anyway.

For time windows ("older than six months", "this quarter"), compute the ISO-8601
`since`/`until` yourself from today's date and pass them.

## Procedure — one batch, one bless

A bulk reorg is a multi-step workflow; stage it as a **single** card:

1. Ground + select the exact entry id set (above). Hold the ids.
2. **`integral_begin_batch`** with a short `label` (e.g. "Archive >6mo") — call it
   **first**, before any create/tag, so everything collects into one card.
3. If the change applies a **new tag** that does not exist yet, **`integral_create_tag`**
   as the **first op INSIDE the batch** (right after `begin_batch`). The tag has no
   id at stage time, so when you tag entries in step 4 set `tag_id` to the literal
   token **`{{tag.id}}`** — it resolves to the real id of the tag created in this
   same batch at approval time. For an **existing** tag, read its real id from
   `integral_get_track_schema` first and use that id directly (no token).
4. Apply the change with the matching bulk/tag tool:
   - **Re-status / patch fields across the set** → `integral_bulk_update_entries(
     entry_ids=[…], updates={…})` — one staged envelope showing the full set.
   - **Move to another quarter/stage by field** → also `integral_bulk_update_entries`
     setting that field (e.g. `{fields:{quarter:"Q4"}}` or `_kanban_stage`).
   - **Tag the set** → `integral_add_entry_tag(entry_id=<id>, tag_id=…)` per entry —
     `tag_id={{tag.id}}` for a tag created in this batch, or the resolved real id for
     an existing tag. (`integral_remove_entry_tag` to clear.)
   - **Archive by delete** → `integral_bulk_delete_entries(entry_ids=[…])` (soft
     delete; reversible by an admin but treat as removal).
5. **`integral_commit_batch`** with a `summary` ("Move 47 Q3 deals → Q4; tag
   `legacy`.") → one combined approval card. **Wait for the bless.**

If the user reconsiders, **`integral_cancel_batch`** — nothing is written.

## Staging discipline

- Every bulk/tag/delete call between `begin_batch` and `commit_batch` is a
  **propose** — it accumulates; it does not apply. The user blesses the whole reorg
  at commit.
- The combined card states the **count and the change** explicitly. Present it that
  way: "Staged: 47 entries Q3→Q4, all tagged `legacy` — approve to apply." Then
  **wait**.
- Bulk tools are **fail-closed per entry**: if the caller cannot edit even one
  entry in the set, the whole batch aborts — never silently partial. Surface that
  error verbatim and re-scope the selection.
- **Never** say "moved", "archived", "tagged", or "done" until
  `[SYSTEM:STAGING-RESOLVED] … state=consumed`. A `revoked` marker means the user
  declined — do not re-stage unasked.

## Forbidden patterns

- Staging a bulk change **without first counting/listing** the set — you must know
  how many entries you are about to touch.
- Looping single-entry `integral_update_entry`/`integral_delete_entry` calls when a
  **bulk** tool exists — that floods the user with cards and risks partial state.
- Mutating without an open **batch** for a multi-step reorg (one card per entry
  instead of one plan).
- Setting a status value, field key, or tag name from memory instead of from
  `integral_get_track_schema`.
- Tagging with a tag that does not exist yet — create it first (in the batch).
- Selecting only the first page (`limit` too low) and acting on a partial set.
- Treating a soft-delete as harmless — confirm the user means to archive the whole
  matched set, and report the count.
- Claiming the reorg happened before the staging-resolved marker.

## Example walkthrough

> **User:** "Archive everything in Notes older than six months and tag it legacy."

1. `integral_list_tracks` → resolve the **Notes** track id.
2. `integral_get_track_schema(track_id=<notes>)` → confirm a `legacy` tag exists;
   it does not yet.
3. Compute `until` = today − 6 months (ISO-8601).
4. `integral_query_entries(track_id=<notes>, until=<6mo ago>, limit=500)` → 38 ids.
5. `integral_count_entries(group_by="track", track_id=<notes>, until=<6mo ago>)` →
   confirm **38**. Tell the user "this matches 38 notes."
6. `integral_begin_batch(label="Archive >6mo Notes")`.
7. `integral_create_tag(name="legacy", track_id=<notes>)`.
8. `integral_add_entry_tag(entry_id=…, tag_id=<legacy>)` ×38 (batched).
9. `integral_bulk_delete_entries(entry_ids=[…38…])` (soft-archive).
10. `integral_commit_batch(summary="Tag 38 notes legacy and archive them.")`.
11. Reply: "Staged: 38 notes older than 6 months tagged **legacy** and archived —
    approve the card to apply." **Wait for bless.**

> **User:** "Move all Q3 deals to Q4."

`integral_query_entries(track_id=<deals>, …Q3 filter…, limit=500)` → ids; confirm
count; `integral_begin_batch` → `integral_bulk_update_entries(entry_ids=[…],
updates={fields:{quarter:"Q4"}})` → `integral_commit_batch`. One card, one bless.
