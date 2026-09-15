---


name: integral_review
description: "Produces periodic synthesis over the workspace — counts, digests, and queries to answer status rollups or recurring reviews, optionally persisting a saved view. Delegates bulk mutations to integral_organize and one-off entry reads to integral_entries."
spec: jv
allowed-tools:
  - integral_list_apps
  - integral_list_tracks
  - integral_get_track_schema
  - integral_query
  - integral_query_entries
  - integral_count_entries
  - integral_get_digest
  - integral_activity_digest
  - integral_save_view
  - integral_list_views
  - integral_delete_view
  - integral_export_view
  # Change-event review scoped to resources the caller can read.
  - integral_query_audit_log
requires-actions:
  - EmbeddedIntegralAction
extends: action:integral/embedded_integral_action
tags:
  - integral
  - review
  - synthesis
  - digest
  - views

---

# Integral review — SOP

## Purpose / when to use

The user wants a **synthesized picture** of where things stand — a rollup, a
status read, a recurring review. Their words sound like:

- "Give me my weekly review."
- "What's the state of the pipeline?"
- "Catch me up on what happened this week."
- "How many open items do I have, by track?"
- "Summarize activity in the Marketing app this month."
- "Save this as my Monday review."

The deliverable is an **answer synthesized from reads** — counts, digests, and
queries woven into a clear summary — and, when the user wants it to recur, **one
write**: a saved view that re-materializes the slice durably.

This skill is **read-heavy by design**. It does not change entries, schema, or
access. Its only mutation is `integral_save_view`.

## When NOT to use this → delegate

- **Changing entries in bulk** as a result of the review (re-status, archive,
  re-tag) → **`integral_organize`**. Review *surfaces* the set; organize *acts* on
  it.
- **Reading or editing one specific entry** → **`integral_entries`**.
- **Open-ended "find anything about X"** concept search as the primary intent →
  **`integral_insights`** owns analytical retrieval; lean on it for ad-hoc
  questions. Review is for *periodic, structured synthesis* that may end in a
  saved view.
- **Designing the schema** the review reads against → **`integral_model`**.
- **Filing new content** → **`integral_filing`**.

If the user just asked a one-off analytical question with no recurring/review
framing and no view to save, `integral_insights` is the better home.

## Grounding — orient before you synthesize

A review is only meaningful against the right scope and shape:

1. **`integral_list_apps`** / **`integral_list_tracks`** — resolve which app(s) and
   track(s) the review covers. "The pipeline" or "my work" must resolve to real
   ids, not assumptions.
2. **`integral_get_track_schema`** — read the status values, entry types, and
   **existing views** for a track before you summarize by them or save a new view.
   You group/filter **by real keys**, never guessed ones.
3. **`integral_list_views`** — check whether a suitable saved view already exists
   before offering to create another; prefer pointing the user at an existing one.

## Procedure — read, synthesize, optionally save one view

1. **Resolve scope** (apps/tracks, above). For time-framed reviews ("this week",
   "last month", "today"), compute the ISO-8601 `since`/`until` yourself from
   today's date — the tools do not know what "this week" means.
2. **Pull the picture** with the right read tool(s):
   - **`integral_count_entries`** — "how many X, broken down by status/track/
     tag/entry_type". `group_by` is required; default `track` for a plain "how
     many" and sum the buckets in your reply.
   - **`integral_activity_digest`** — a rolled-up digest over a `period`
     (today/week/month), scoped to user/app/track. Best for "what's happening" /
     "morning digest" framing.
   - **`integral_get_digest`** — the itemized, chronological event stream for
     "show me the actual recent items / catch me up".
   - **`integral_query_entries`** — the structured list for "the open deals updated
     this month", "blocked tasks". Raise `limit` if you must rank or total a set,
     and read the field yourself (no sum/avg/max tool exists).
   - **`integral_query`** — semantic recall only when the question is concept-based.
3. **Synthesize** — weave the reads into a clear, scoped summary. State the
   numbers, the notable items, and the window. Do not pad with data the user did
   not ask for.
4. **Offer to persist** (optional, the one write) — when the review is recurring or
   the user says "save this": **`integral_save_view(track_id, name, view_type,
   config)`** so the slice becomes a durable tab. Pick a `view_type`
   (`feed`/`kanban`/`table`/`calendar`/`gallery`) that matches what they saw, and
   set `config` (filters, `group_by`, `entry_type_keys`) to re-create it.

## Staging discipline

- The reads are **op-class read** — no approval, no staging. Answer directly from
  their results.
- `integral_save_view` is the **one propose** in this skill — it stages a saved
  view the user blesses; there is no separate execute step. Present it as "I can
  save this as a *‹name›* view" and **wait** for the bless before calling it saved.
- Never claim a view "exists" until `[SYSTEM:STAGING-RESOLVED] … state=consumed`.
- Surface any read error envelope verbatim; do not fabricate a synthesis over a
  failed query.

## Forbidden patterns

- Synthesizing a review **without resolving scope** — never report on "the
  pipeline" without resolving the actual track(s) via `integral_list_tracks`.
- Grouping or filtering by a **status/tag/entry-type value you did not read** from
  `integral_get_track_schema`.
- Forgetting to compute `since`/`until` for a time-framed review and silently
  reporting an all-time number as "this week".
- Calling `integral_count_entries` without a `group_by`.
- **Mutating entries** as part of a review — review reads and (at most) saves a
  view; bulk changes belong to `integral_organize`.
- Saving a **duplicate** view when `integral_list_views` shows an equivalent one
  already exists.
- Claiming a saved view was created before the staging-resolved marker.

## Example walkthrough

> **User:** "Give me my weekly review of the sales pipeline, and save it."

1. `integral_list_tracks` → resolve the **Deals** track (the pipeline).
2. `integral_get_track_schema(track_id=<deals>)` → stage values: `lead`,
   `qualified`, `won`, `lost`; an existing `kanban` view, no "Weekly Review" view.
3. Compute `since` = start of this week (ISO-8601).
4. `integral_count_entries(group_by="status", track_id=<deals>)` → counts per
   stage.
5. `integral_activity_digest(scope="track", scope_id=<deals>, period="week")` →
   what moved this week.
6. `integral_query_entries(track_id=<deals>, statuses=["qualified"], since=<this
   week>, limit=100)` → the active-this-week shortlist.
7. **Synthesize:** "Pipeline this week: 4 leads, 6 qualified, 2 won, 1 lost. Three
   deals advanced to qualified; *Acme* moved to won. Open focus: …"
8. `integral_save_view(track_id=<deals>, name="Weekly Review", view_type="kanban",
   config={group_by:"status"})` → stage for bless.
9. Reply with the synthesis, then: "I've staged a **Weekly Review** board grouped
   by stage — approve to keep it." **Wait for bless.**

> **User:** "How many open tasks do I have?"

`integral_list_tracks` → `integral_get_track_schema` (confirm `open` is a real
status) → `integral_count_entries(group_by="track", status="open")` → sum the
buckets and answer in one line. No view to save unless they ask.
