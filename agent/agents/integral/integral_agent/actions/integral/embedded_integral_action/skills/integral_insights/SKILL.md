---


name: integral_insights
description: "Queries, analyzes, ranks, and synthesizes across the user's Integral substrate — counts, superlatives, breakdowns, comparisons, and activity digests. Use for \"what's happening\", top/bottom rankings, and saving a useful query as a View."
spec: jv
allowed-tools:
  - integral_describe_capabilities
  - integral_governed_query
  - integral_query_spec
  - integral_query
  - integral_query_entries
  - integral_count_entries
  - integral_activity_digest
  - integral_get_digest
  - integral_save_view
  # Cross-domain reads this SOP names to scope a query/digest (resolve the
  # target app/track/entry before counting or filtering) — surfaced here so
  # the insights flow is self-sufficient without a companion skill active.
  - integral_list_apps
  - integral_list_tracks
  - integral_resolve_entry
  - integral_describe_substrate
  - integral_get_feed
  - integral_list_notifications
  - integral_mark_notification_read
  # Workspace-wide retrieval across all readable tracks ("find anywhere").
  - integral_search_cross_track
# requires-actions (jvagent skill standard): the Action type whose get_tools()
# furnishes every integral_* tool this SOP coordinates.
requires-actions:
  - EmbeddedIntegralAction
extends: action:integral/embedded_integral_action
tags:
  - integral
  - insights
  - query
  - analytics

---

# Integral insights — SOP

## When to use

The user is asking about **state** of their substrate — "what's
overdue," "how many open bugs," "what happened last week,"
"compare Marketing vs Engineering this month," "who owns the budget
work." That's the **insight mode**.

This is distinct from individual entry reads in `integral_entries`:

- **`integral_query_entries`** / **`integral_resolve_entry`** (in
  `integral_entries`) — fetch specific known entries inside a known
  track. Use when the user references a known target.
- **`integral_query`** — unified hybrid retrieval (graph / semantic /
  hybrid) across everything the caller can read. This is the primary
  open-ended retrieval tool: use it for "find anything about X,"
  cross-track concept search, or "what relates to Y" when the user
  isn't naming one track.
- **`integral_query_spec`** — deterministic, bounded Core queries over
  entries, tracks, or apps with explicit projection, filters, sorting,
  and at most one graph hop. Use when the answer depends on exact
  structured fields rather than semantic relevance; retain its
  `result_set_id` and receipt as the provenance link for the result.
- **`integral_query_entries`** — filtered query within a track
  (`track_id`, `query`, `tags`, `entry_type`, `limit`). Use when the
  user asks a structured "show me all X in this track" or "what matches
  Y" question.
- **`integral_count_entries`** — group-by counts (by track, status,
  tag, or entry_type). Use for "how many X" or "what's the breakdown."
- **`integral_activity_digest`** — recent-activity summary (per-track
  recent-touch summaries for a scope + period). Use for "what's been
  happening" or "morning digest" questions.
- **`integral_get_digest`** — on-demand activity digest for a scope +
  period that itemizes entries created / modified, comments, and
  mentions. Reach for this when the user wants the granular "what
  changed and who touched it" rundown rather than the per-track
  rollup.

## When NOT to use — delegate

- **Creating or modifying individual entries** → skill `integral_entries`.
- **Filing freeform user-typed content** → skill `integral_filing`.
- **Shaping Operational Model schema** → skill `integral_models`.
- **Acting on one item a briefing surfaced** (open, comment, update) →
  skill `integral_entries`.

## Procedure

The insight loop: **ground → plan query → execute → synthesize →
optionally save as a view.**

1. **Ground first.** If you don't already know the user's tracks /
   apps, list them via `integral_list_tracks` (or `integral_list_apps`,
   from the `integral_workspace` skill). Don't query against tracks you
   haven't verified.

2. **Plan the query.** Decide the right tool AND the right filters:

   **Choosing a search mode.** For concept / meaning search reach for
   `integral_query` (semantic when available). It reports `mode` /
   `degraded` in its result — and `integral_describe_substrate` exposes
   `retrieval.semantic_available` up front. When semantic is OFF (the
   substrate says so, or the result comes back `degraded: true`), the
   text tools match by KEYWORD TERM OVERLAP, not meaning: phrase the query
   as concise keywords (multi-word is fine; terms are split and matched
   any-of — no boolean `OR` needed) and never expect a value ranking from
   search (use the fetch-and-reason superlative flow below for that).

   - Open-ended "find anything about X" / concept search across the
     substrate → `integral_query` (hybrid retrieval; pass `query` and
     optionally `mode` / `scope` / `filters`).
   - Specific filter within a track → `integral_query_entries` with
     `track_id` plus `tags` / `entry_type` / `query`.
   - "How many" question → `integral_count_entries` with the
     appropriate `group_by` (track / status / tag / entry_type); this
     one accepts a `since` / `until` time window.
   - "Recent activity" / "what's been happening" → a digest scoped by
     `scope` / `scope_id` / `period`: `integral_activity_digest` for the
     per-track rollup, or `integral_get_digest` when the user wants the
     itemized created / modified / commented / mentioned breakdown.
   - **Superlative / ranking** — "the most lucrative / highest-revenue /
     biggest / top / largest / oldest / smallest X", or "rank X by Y".
     There is **no aggregate-by-field tool** and `integral_count_entries`
     only COUNTS rows (it cannot sum / max a field); `integral_query`
     is *semantic* and will NOT rank by a value — searching for the word
     "lucrative" finds nothing because the ranking lives in a numeric
     field, not the text. So answer superlatives by FETCH-AND-REASON:
       1. Ground the target track (e.g. the "Projects" track) via
          `integral_list_tracks` — do not assume it.
       2. `integral_query_entries` on that `track_id` to pull the candidate
          entries (raise `limit` so you see the full set, not a page).
          If the ranking is by a built-in field (updated/created date or
          title), pass `sort_by`/`sort_dir` and the top row is your answer.
       3. If you rank by a **custom field** (e.g. `total_amount` / budget /
          value), note the rows from step 2 are SUMMARIES and do NOT carry
          custom fields — call `integral_resolve_entry` on the top
          candidates to read that field, then compute the max / min /
          ranking YOURSELF and name the winner with its value.
       4. Never answer "I couldn't find it" off a single semantic
          `integral_query` miss — that tool is the wrong instrument for a
          value ranking; fall back to steps 1–3.

   **Track-named queries always use `track_id`, never `entry_type`.**
   When the user names a track in their question — "our recent
   opportunities", "show me contacts", "list bugs" — the right
   filter is `track_id` (resolved from the named track), not
   `entry_type`. You can pass the track NAME directly as `track_id`;
   the backend resolves it case- and substring-tolerantly against
   the user's accessible tracks. Reserve `entry_type` for the
   "WITHIN this track, only show me X entries" case.

3. **Execute.** Time windowing differs by tool:
   - `integral_activity_digest` / `integral_get_digest` accept `period`
     shortcuts (`today` / `week` / `month`) — use these for
     "this week" / "last 7 days" style activity questions.
   - `integral_count_entries` accepts a `since` / `until` window
     (ISO-8601 dates or datetimes, e.g. `"2026-04-01"` or
     `"2026-04-01T12:00:00Z"`). For a time-bound count you MUST compute
     `since` yourself from today's date — the tool does not know what
     "today" means.
   - `integral_query` / `integral_query_entries` filter by content and
     track, not by an explicit date range; if the user wants
     "recent X," lead with a digest or sort the results you get.

4. **Synthesize and PRESENT — required, not optional.** After
   the tool returns data, you MUST list the entries the tool
   returned, by title, in your reply. A reply that omits the data
   is a failed turn.

   - `integral_query` / `integral_query_entries`: "Your N most recent
     X: 1) <Title> (updated <date>), 2) <Title>, …" For >5 entries,
     list the first 5 and add "…plus M more".
   - `integral_count_entries`: "N total X. Breakdown: Track A (n),
     Track B (n), …"
   - `integral_activity_digest` / `integral_get_digest`: "Last
     <period>: N entries across M tracks. Most active: Track A (n),
     Track B (n). Recent highlights: <title>, <title>."

   **Never thank the user for "sharing" the data** — you ran the
   query. Don't reply with only an offer to "help further" while
   omitting the data the tool returned.

5. **Offer to save.** If the user found the result useful — or if
   they explicitly say "save this view" — call `integral_save_view` to
   materialize the query as a persisted View on the relevant track. It
   stages a save the user approves in Integral; there is no separate
   execute step — when the user blesses it, the View is created.

### Save view pattern

`integral_save_view` takes `track_id`, a **required** `name` (the
display name for the view — the stager raises if neither `name` nor
`title` is supplied), `view_type` (`feed` / `kanban` / `table` /
`calendar` / `gallery`), an optional `view_id` (pass it to update an
existing view; omit to create a new one), and an optional `config` dict
for filters / sort / group_by / entry_type_keys. Pass a config that
*roughly* re-creates the query the user just saw — exact view-config
schemas vary by view_type and can be refined later in the UI.

### Briefing & rollup — "catch me up"

A **briefing** is the digest mode pointed at a person's attention: "catch
me up", "what happened today / this week", "give me my morning digest",
"what's new across my workspace", "what needs my attention". It rolls up
recent activity rather than answering a single filtered query.

- **Use here:** time-windowed recency rollups and "what changed" rundowns.
- **Within this skill:** a precise "how many / breakdown"
  question → `integral_count_entries`; a "find anything about X" concept
  search → `integral_query`; a superlative/ranking → the fetch-and-reason
  flow above.

**Tools — two live digest reads:**

- `integral_activity_digest` — per-track **rollup** (entry counts +
  recently-touched titles) for a `scope` (`user` / `app` / `track`),
  `scope_id`, and `period` (`today` / `week` / `month`). Best for the
  one-paragraph "morning digest".
- `integral_get_digest` — the **itemized** stream of individual events
  (entries created / modified, comments, mentions), newest first,
  cursor-paginated, optionally narrowed by `track_id` / `app_id`. Best
  for "show me exactly what changed and who touched it".

Pick the rollup for a summary, the itemized digest for the granular
rundown. Both are pure reads — nothing stages.

Use `integral_get_feed` for a cross-track chronological slice and
`integral_list_notifications` for the caller's notification inbox. Use
`integral_mark_notification_read` only after the user asks to clear or
acknowledge a notification. These complement, rather than replace,
`integral_activity_digest` and `integral_get_digest`: pick the surface that
matches the user’s question and state its source plainly.

**Present the briefing:** lead with the headline counts, then the
highlights — never an empty "here's a summary" with no items: "Last
<period>: N entries across M tracks. Most active: Track A (n), Track B (n).
Recent: <title>, <title>, <title>." Cite only what the digest returned
this turn.

## Staging discipline

- The reads (`integral_query` / `integral_query_entries` /
  `integral_count_entries` / `integral_activity_digest` /
  `integral_get_digest`) never stage — they're pure reads.
- `integral_save_view` is a **propose** tool: it stages a change the
  user approves in Integral. There is **no separate execute step** —
  when the user blesses the staged View, the backend creates it.
- A propose tool returns a staged-change envelope; the
  `_kind == "staged_change"` sentinel tells the chat surface to render
  an approval card. In that turn your job is **just** to present and
  wait. Once the user blesses it, Integral applies the change (the
  resident threads the originating `interaction_id` so the
  `[SYSTEM:STAGING-RESOLVED]` closure marker fires on the right turn).
- If a propose call returns an error envelope, surface it verbatim —
  never retry blindly or pretend a write happened.

## Grounding

- Cite entry titles, ids, and counts the API actually returned this
  turn — never carry numbers from earlier conversations.
- For aggregates, surface the `total_matched` and `filters_applied`
  from the response so the user can verify the question was
  interpreted correctly.
- If a query returns zero matches, say so plainly and consider
  whether the filter was too narrow — offer to re-run with relaxed
  predicates rather than fabricating examples.

## Forbidden patterns

- Replying with only an offer to "help further" while **omitting the
  data** the query tool returned — synthesis is mandatory.
- Thanking the user for "sharing" data you fetched yourself.
- Using `integral_query` (semantic search) for **superlative / ranking**
  questions — use fetch-and-reason via `integral_query_entries` +
  `integral_resolve_entry` instead.
- Using `entry_type` when the user named a **track** — resolve
  `track_id` from the track name.
- Claiming a View was saved before the user blesses the
  `integral_save_view` staged card.
- Calling gap tools (`integral_get_feed`, `integral_list_notifications`)
  or presenting capabilities they would provide.
- Fabricating entry titles, counts, or rankings not returned by tools
  this turn.

## Example

> **User:** "How many open deals do we have, broken down by track?"

1. `integral_list_tracks` → confirm the Deals-related tracks and their
   ids (do not assume track names).
2. `integral_count_entries(group_by="track", status="open",
   entry_type="deal")` → read `total_matched` and the per-track
   breakdown from the response.
3. **Present:** "You have 14 open deals. Breakdown: Pipeline (9),
   Renewals (5)." Cite only what the tool returned.
4. If the user says "save this as a view" → `integral_save_view(
   track_id=<pipeline track id>, name="Open Deals", view_type="table",
   config={filters: {status: "open", entry_type: "deal"}})` → stage
   the card and **wait**; do not claim the view exists until blessed.

> **User:** "What's our highest-value deal?"

1. `integral_list_tracks` → resolve the Deals track id.
2. `integral_query_entries(track_id=<deals>, entry_type="deal",
   limit=50)` → pull the candidate set (summaries only — no custom
   fields).
3. `integral_resolve_entry` on the top candidates → read the `value`
   field from each; compute the max yourself.
4. **Present:** "Your highest-value deal is *Acme Renewal* at
   $240,000." Name the winner with the value you read — never guess
   from semantic search.
