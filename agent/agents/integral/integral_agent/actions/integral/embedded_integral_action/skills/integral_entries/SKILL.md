---

name: integral_entries
description: Reads and manages entries inside the user's Integral tracks — create, update, delete, tag, comment, and wire relation fields.
spec: jv
allowed-tools:
  - integral_query_entries
  - integral_resolve_entry
  - integral_create_entry
  - integral_update_entry
  - integral_delete_entry
  - integral_add_comment
  - integral_list_comments
  - integral_edit_comment
  - integral_delete_comment
  - integral_get_related
  - integral_list_tags
  # Tagging an entry (TAGGED_WITH edge add/remove) + ad-hoc tag creation.
  - integral_add_entry_tag
  - integral_remove_entry_tag
  - integral_create_tag
  # Wire a relation field on an entry → REFERENCES (entry) / ANCHORS (track) edge.
  - integral_link_entries
  # Bundle entry.transform (e.g. won opportunity → project). CRM/Sales SOPs
  # also name this; listed here so the catalogue is not orphaned.
  - integral_transform_entry
  # Named in this SOP to locate the target track before reading/creating
  # entries, and to read a track's schema for valid tags / relation field keys.
  - integral_list_tracks
  - integral_get_track_schema
# requires-actions (jvagent skill standard): the Action type whose get_tools()
# furnishes every integral_* tool this SOP coordinates. Declares the hard
# dependency so the skill only activates when the embedded surface is present.
requires-actions:
  - EmbeddedIntegralAction
extends: action:integral/embedded_integral_action
tags:
  - integral
  - entries
  - crud
---

# Integral entries — SOP

## Workflow

1. Identify the target track first. If the user names a track without
   giving an id, call `integral_list_tracks` (from the
   `integral_workspace` skill) to disambiguate before creating or
   modifying entries.
2. **View-aware creates** — when the user names a view tab (calendar,
   board, feed, table, …) or the UI has a focused view:
   - Call `integral_get_track_schema` for that track **before** staging.
   - Pass `view_hint` (view name, e.g. "Calendar") or `view_id` from
     the schema's `views` list to `integral_create_entry`.
   - Pass `focused_view_id` when you know the active tab from UI context.
   - **Calendar:** use an entry type that declares the view's mapped date
     field (see `views[].config.calendar_mapping`) and set that field in
     `fields` with an ISO date (`yyyy-mm-dd`) parsed from the user's
     message (e.g. "on 19 June" → `target_date: "2026-06-19"`).
   - **Kanban / board:** set `_kanban_stage` (or the workflow field the
     board groups on) when the user names a column — see Kanban section
     below.
   - Do **not** assume `Post` — use entry-type slugs from the schema
     (`goal`, `content_piece`, etc.).
3. Reads (no confirmation needed):
   - `integral_query_entries` — the filtering workhorse. Cross-track by
     default; pass `track_id` (id or NAME) to scope to one. Filter with
     `query` (text), `status`/`statuses`, `tags`, `entry_type`,
     `since`/`until` (ISO dates — compute them yourself for "this week"
     etc.), and order with `sort_by`/`sort_dir`; page with
     `limit`/`offset`. Use it for "show me all X", "open items tagged
     Y", and — since there is no aggregate-by-field tool — for
     **superlatives/rankings** ("the most/biggest/highest/top X"): raise
     `limit` to pull the candidate set, then RANK. Note the returned rows
     are SUMMARIES (id, title, status, tags, type, timestamps); to rank
     by a CUSTOM field (e.g. a deal's `value`/amount) call
     `integral_resolve_entry` on the top candidates to read it. Never
     give up after one empty search.
   - When you mention **any** entry by title in your reply — lists ("last 3
     entries"), singles, or search results — **always** format it as a
     markdown link. Use `action_url` from the tool result when present, or
     build `/tracks/{track_id}?entry={entry_id}`. Plain-text titles alone
     are forbidden. See `integral_navigation`.
   - `integral_resolve_entry` — resolve a single entry by `entry_id`
     when you need full detail: its fields (incl. custom fields),
     tags, relations, comment count, and backlinks. (If the user named
     the entry by title, resolve the id first via
     `integral_query_entries`.)
4. Mutations are **propose** tools: you call a single tool, it
   **stages** a change the user approves in Integral. There is **no
   separate execute step** — when the user blesses the staged change in
   the chat surface, the backend applies it. Present the staged card
   and wait; never apply the change yourself.
   - **Create** — call `integral_create_entry` with `text` (the entry
     content) plus a routing hint: `track_hint` (track name) or
     `focused_track_id` to target a known track, optional `type_hint`,
     `title`, `fields`, and when the user named a view tab,
     `view_hint` / `view_id` / `focused_view_id`. It stages a create the
     user approves in Integral. The result is a staged-change envelope; do
     not claim the entry exists until the user has blessed it (a session
     autonomy grant may auto-approve with an undo button, but that is the
     user's setting, not your call).
   - **Update** — call `integral_update_entry` with `entry_id` and an
     `updates` object carrying only the fields the user wants changed.
     The approval card renders before/after for any changed top-level
     field. It stages an update the user approves in Integral.
   - **Delete** — call `integral_delete_entry` with `entry_id`. The
     approval card explicitly states the deletion is irreversible. It
     stages a delete the user approves in Integral. Never narrate a
     delete as done before the user blesses it; even if the user
     deleted similar entries earlier this session, an approval card is
     still surfaced.
   - **Comment** — call `integral_add_comment` with `entry_id` and the
     comment `text` to post a comment on an entry (e.g. when the user
     asks to leave a note or flag something on a record). `@mentions`
     in the text resolve to MENTIONS edges and notifications. It stages
     the comment the user approves in Integral.

### Staging discipline

- A propose tool returns a staged-change envelope; it does **not**
  apply the change. Present it and wait — do not claim the create /
  update / delete / comment succeeded until the user has blessed it.
- The `_kind == "staged_change"` sentinel in the result tells the chat
  surface to render an approval card; if you see that sentinel, your
  job in this turn is **just** to present and wait. Once the user
  blesses it, Integral applies the change (the resident threads the
  originating `interaction_id` so the `[SYSTEM:STAGING-RESOLVED]`
  closure marker fires on the right turn).
- If a propose call returns an error envelope, surface it verbatim —
  never retry blindly or pretend a write happened.

### Kanban column moves

Tracks with a **kanban** view place cards using the system field
`_kanban_stage` (values match column keys: `todo`, `in_progress`,
`in_review`, `done`, etc.). Do **not** use `status`, `Status`, or
`stage` in `fields` unless the entry type's profile explicitly defines
that field (most bare `Post` tracks do not).

Example — move a card to In Progress:

```json
{ "fields": { "_kanban_stage": "in_progress" } }
```

### Constraints

- Do not invent `track_id` or `entry_id`. If the user names something
  by title, list-and-confirm before acting.
- Surface error envelopes
  (`{"error": true, "error_code": ..., "message": ...}`) verbatim —
  never claim success on an error response.
- For attachments (associating files with an entry), defer — those tools
  are specified in the manifest but not yet dispatchable (see Tags,
  Comments & Relations below). Tags, comments, and relations ARE covered
  here.

## Tags, comments & relations

These three extend an entry beyond its own fields: a **tag** labels it, a
**comment** is discourse on it, and a **relation** wires it to another
entry or track. All are **propose** tools — they stage and the user
blesses; nothing is applied until then.

### When to use → delegate

- **Use here:** tag / untag a known entry, post or read comments on a
  known entry, link one entry to another (or anchor a companion track).
- **Delegate to `integral_workspace`:** sharing / access (who can SEE the
  entry) — a collaborator grant is not a tag.
- **Delegate to `integral_profiles`:** defining which tags or relation
  fields an entry type *offers* (schema), vs. assigning them on one entry.
- **Delegate to `integral_insights`:** "how many entries tagged X",
  group-by-tag breakdowns — that's analytics, not per-entry tagging.

### Grounding — read the schema first

Tags and relation fields are **profile-defined**: a track's content
profile declares which tags exist and which relation fields an entry type
offers. Never invent a tag name or relation `field_key` from memory.

- `integral_get_track_schema(track_id=…)` — read `tags` (available tag
  names/ids) and each entry type's `form_schema` to find `relation`
  fields and their `field_key`s (and whether the target is an entry or a
  track). Do this **this turn** before tagging or linking.
- `integral_resolve_entry(entry_id=…)` — confirm the entry's current
  tags and relations before adding/removing, so you don't duplicate.

> **Not-yet-available:** a tag lister (`integral_list_tags`), a comment
> lister (`integral_list_comments`), and a relation walker
> (`integral_get_related`, "show me the Contacts linked to this Project")
> are specified in the tool manifest but are **not yet dispatchable**
> (status: gap). Until they ship: read available tags from
> `integral_get_track_schema`; read existing comments and relations from
> `integral_resolve_entry` (it returns comment count + the entry's
> relations/backlinks). Do **not** call these gap names or claim a
> capability they would provide that you can't reach today.

### Procedure — tagging

1. Resolve the entry id (`integral_query_entries` / `integral_resolve_entry`).
2. Resolve the tag id from `integral_get_track_schema`'s `tags`. If the
   user names a tag that doesn't exist yet, you may stage one with
   `integral_create_tag` (`name`, plus a scope — `track_id` or `app_id`,
   optional `parent_tag_id` / `color`) — this is itself a propose.
3. `integral_add_entry_tag(entry_id, tag_id)` to attach the `TAGGED_WITH`
   edge, or `integral_remove_entry_tag(entry_id, tag_id)` to detach it.
   Each stages one card.

### Procedure — comments

- `integral_add_comment(entry_id, text)` posts a comment. `@mentions` in
  the text resolve to MENTIONS edges + notifications. A commenter-level
  role is enough. It stages the comment the user blesses.
- To *read* existing comments, use `integral_resolve_entry` (comment
  count + backlinks) until the dedicated `integral_list_comments` ships.

### Procedure — relations (link entries / anchor tracks)

`integral_link_entries` sets a **relation field** on a source entry,
which materializes the graph edge — `REFERENCES` for an entry target,
`ANCHORS` for a track target — carrying the `field_key`. A scalar id in a
plain field is **not** a substitute; the relation edge is the source of
truth (I-GRAPH-01).

1. Read the source entry's type schema (`integral_get_track_schema`) to
   find the relation `field_key` and whether it targets an `entry` or a
   `track`. The field must exist on the source entry type — you cannot
   invent one here (that's a profile change → `integral_profiles`).
2. Resolve the source entry id and the target id (target entry via
   `integral_query_entries`; target track via `integral_list_tracks`).
3. `integral_link_entries(source_entry_id, field_key, target_id)`. It
   stages the link the user blesses.

Use a **lookup** relation (target: entry — e.g. Project → Contact) or an
**anchor** relation (target: track — e.g. Project → its Project-Details
track), per what the field's schema declares — never both for the same
relationship.

### Staging & forbidden patterns (tags / comments / relations)

- Same staging discipline as above: present the card, wait for bless;
  never narrate "tagged" / "commented" / "linked" until
  `[SYSTEM:STAGING-RESOLVED] … state=consumed`.
- Don't invent tag names or relation `field_key`s — read them from the
  track schema this turn.
- Don't stuff a related entry's id into a plain text field to "link" it —
  use `integral_link_entries` so the edge is real.
- Don't `integral_create_tag` when an equivalent tag already exists in the
  schema — reuse it.

## Attachments

`integral_resolve_entry` returns a summary of an entry's attachments. To list
an entry's files, read a document's extracted text, or deliver a download
link, hand off to **`integral_attachments`** — don't read file content here.

## Scope

This skill is for entries inside a known track. App-level
discovery (apps / tracks themselves) belongs in `integral_workspace`.
For filing freeform content where the track/type is inferred, use
`integral_filing`. For an entry's attached files, use `integral_attachments`.

## Grounding

- When speaking to the user (chat prose, summaries, confirmations), prefer
  entry **titles** and tag **names** over raw node ids (`n.Entry.*`,
  `n.Tag.*`). Reserve ids for disambiguation when two items share the same
  title or name.
- Only cite entry titles and ids the API has actually returned in this
  turn.
- If the active user has no entries (or none matching the query), state
  that plainly rather than fabricating examples.

## Example

> **User:** "Create a goal in Projects called 'Launch v2' due June 30."

1. `integral_list_tracks` → resolve the Projects track id by name.
2. `integral_get_track_schema(track_id=<projects>)` → confirm the `goal`
   entry type exists and read the date field key from the calendar mapping
   (e.g. `target_date`).
3. `integral_create_entry(
     track_hint="Projects",
     type_hint="goal",
     title="Launch v2",
     text="Launch v2 by end of June.",
     fields={target_date: "2026-06-30"}
   )` → stage the create; present the card and **wait**.
4. Only after `[SYSTEM:STAGING-RESOLVED] … state=consumed` may you say
   the goal was created. If the user then asks to move it on the board,
   `integral_update_entry(entry_id=<id>, updates={fields:
   {_kanban_stage: "in_progress"}})` → stage again and wait.
