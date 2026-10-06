---
name: integral-insights
description: Search existing Integral records and current workspace state to answer one-time questions. Find whether information is already filed, search entries by meaning, summarize, count, rank, compare, or break down current data; optionally save a query as a View. For a new operational app, use integral-scaffold. For periodic or recurring review deliverables, use integral-review.
allowed-tools: integral_describe_capabilities integral_governed_query integral_query_spec integral_plan_query integral_query integral_query_entries integral_count_entries integral_aggregate integral_activity_digest integral_get_digest integral_save_view integral_list_apps integral_list_tracks integral_get_track_schema integral_resolve_entry integral_describe_substrate integral_get_feed integral_list_notifications integral_mark_notification_read integral_search_cross_track integral_list_tags
---

# Integral insights — SOP

## When to use

The user is asking about **state** of their substrate — "what's
overdue," "how many open bugs," "what happened last week,"
"compare Marketing vs Engineering this month," "who owns the budget
work." That's the **insight mode**.

This is distinct from individual entry reads in `integral-entries`:

- **`integral_query_entries`** / **`integral_resolve_entry`** (in
  `integral-entries`) — fetch specific known entries inside a known
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
  (`track_id`, `query`, `tags`, `entry_type`, `filters`, `limit`). Its rows
  include `custom_fields`; use an exact map such as
  `{ "custom_fields.priority": "High" }` when the user asks about a
  model-defined field. Use when the
  user asks a structured "show me all X in this track" or "what matches
  Y" question. An installed App's records are not in these generic results.
  A `refused` or `boundary` field means that part needs the App's own
  declared query. Do not tell the user there are no records.
- **`integral_count_entries`** — group-by counts (by track, status,
  tag, entry_type, or date — creation day). Use for "how many X" or
  "what's the breakdown." It cannot group by a model-defined field
  and it cannot sum a value. Totals go to `integral_aggregate`.
- **`integral_aggregate`** — exact `count` / `sum` / `avg` / `min` /
  `max` / `distinct` over open-class entries, including a custom field
  and `date:<field>` buckets. Pass `timezone` for datetime buckets.
  A `refused` or `over_budget` result is the answer; do not invent a
  total from a page of rows, and do not say the track is empty.
- **`integral_activity_digest`** — recent-activity summary (per-track
  recent-touch summaries for a scope + period). Use for "what's been
  happening" or "morning digest" questions.
- **`integral_get_digest`** — on-demand activity digest for a scope +
  period that itemizes entries created / modified, comments, and
  mentions. Reach for this when the user wants the granular "what
  changed and who touched it" rundown rather than the per-track
  rollup.

## When NOT to use — delegate

- **Creating or modifying individual entries** → skill `integral-entries`.
- **Filing freeform user-typed content** → skill `integral-filing`.
- **Shaping Operational Model schema** → skill `integral-models`.
- **Acting on one item a briefing surfaced** (open, comment, update) →
  skill `integral-entries`.

## Procedure

Choose the simplest read that answers the request, execute it, and present
its result. Ordinary reads should not become multi-call investigations.

1. **Choose by outcome.** For open-ended "find anything about X" or
   "is anything filed about X?" questions, call integral_query directly.
   It searches readable entries across tracks. Do not first list
   workspaces, apps, or tracks, inspect schemas, or call
   integral_plan_query. Narrow by a track or app only when the user names
   it. Use integral_query_entries for specific filtered records,
   integral_count_entries for counts, integral_aggregate for exact field
   calculations, integral_query_spec for bounded structured reads, and
   digest tools for recent activity. Resolve an ID or field key only when
   the selected operation needs it.

2. **Stop when the result is authoritative.** A successful, non-degraded
   integral_query answers an open-ended concept search, including when it
   returns zero matches. Report that no matching readable entries were
   found and stop. Do not repeat it with keyword search, inventory the
   workspace, or claim that no information exists anywhere unless the
   result establishes that. Use one relevant fallback only when the result
   is degraded, refused, or says it could not answer. If a result identifies
   an App-owned data boundary, search that App only when an authorized
   matching capability is available; never invent an App result. When the
   search covers Core-readable entries only, keep the answer scoped to those
   entries and do not imply App-owned records were searched.

3. **Use specialized reads when needed.** integral_plan_query is for
   structured or multi-step queries that need explicit instrument
   selection, field mapping, or a reusable result set. It is not a
   prerequisite for ordinary retrieval. Never plan a read and then repeat
   it through another tool. Preserve a result_set_id when continuing a
   query that returned one. Treat expired, wrong_principal,
   wrong_workspace, schema_drift, refused, error, or over_budget as
   authoritative outcomes.

   - Concept or meaning search → integral_query; inspect its mode and
     degraded fields. If semantic retrieval is unavailable or degraded,
     concise keyword retrieval may be a useful fallback.
   - Specific filters within a track → integral_query_entries; resolve
     track or tag IDs only when needed. Track names can be passed directly
     where the tool accepts them.
   - "How many" → integral_count_entries with the appropriate grouping.
   - Exact total, average, minimum, or maximum → integral_aggregate.
   - Recent activity → integral_activity_digest for a per-track rollup
     or integral_get_digest for itemized changes.
   - **Superlative or ranking** — "highest value", "largest", "oldest",
     or "rank by Y" — use one sorted integral_query_spec call (or
     integral_query_entries for a built-in field); semantic search does
     not order by field value. Resolve the track and schema only if those
     IDs or keys are required for the sort. Resolve custom-field schema keys
     before ranking; never display labels as field keys, and never rank over a truncated page.

4. **Execute.** Time windowing differs by tool:
   - `integral_activity_digest` / `integral_get_digest` accept `period`
     shortcuts (`today` / `week` / `month`) — use these for
     "this week" / "last 7 days" style activity questions.
   - `integral_count_entries` and `integral_aggregate` accept a `since` / `until` window
     (ISO-8601 dates or datetimes, e.g. `"2026-04-01"` or
     `"2026-04-01T12:00:00Z"`). For a time-bound count you MUST compute
     `since` yourself from today's date — the tool does not know what
     "today" means.
   - `integral_query_entries` also accepts `since` / `until`; like the
     count window, they bound the entry's last update (or creation).
   - A **model-defined date field** ("due this week", "expiring before
     June") is a range on that field, not an update window. Pass
     `integral_query_entries` `filters` as a list, for example
     `{field: "custom_fields.due_date", op: "gte", value: "2026-04-01"}`
     and `op: "lte"` for the upper bound. `integral_query_spec` and
     `integral_count_entries` take that same list. Compute the ISO dates
     yourself. An exact date uses `op: "eq"`.
   - `integral_query` filters by content and scope, not by date.

5. **Synthesize and PRESENT — required, not optional.** After
   the tool returns data, you MUST list the entries the tool
   returned, by title, in your reply. A reply that omits the data
   is a failed turn. Answer only the user's question and stop. Do not
   volunteer to create an App, propose a new workflow, or offer unrelated
   setup just because a search returned no matches; use integral-scaffold
   only when the user asks to design or create a system.

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

6. **Offer to save.** If the user found the result useful — or if
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
re-creates the query the user just saw.

`config.filters` is a **list** of `{field, op, value}` objects —
never a map. `operator` is accepted and stored as `op`. `op` is one of
`eq`, `neq`, `in`, `not_in`, `contains`, `gt`, `lt`, `gte`, `lte`,
`exists`. `in` and `not_in` take a list and keep only the records inside
or outside that list. An unknown `op` is refused before the view is
staged. Model-defined fields use `custom_fields.<key>`. `config.sort` is
a list of `{field, direction}`.
Narrow by entry type with `config.entry_type_keys`, not a filter.

### Briefing & rollup — "catch me up"

A **briefing** is the digest mode pointed at a person's attention: "catch
me up", "what happened today / this week", "give me my morning digest",
"what's new across my workspace", "what needs my attention". It rolls up
recent activity rather than answering a single filtered query.

- **Use here:** time-windowed recency rollups and "what changed" rundowns.
- **Within this skill:** a precise "how many / breakdown"
  question → `integral_count_entries`; a "find anything about X" concept
  search → `integral_query`; a superlative/ranking → one sorted
  `integral_query_spec` call, as above.

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
- If a query returns zero matches, report that result plainly and state
  the search boundary when it matters. Do not imply unsearched App-owned
  records were covered, and do not offer to broaden the search unless the
  user asks or the returned result identifies an unresolved boundary.
- never fill values in from memory; use only field values returned by the
  current query.

## Forbidden patterns

- Replying with only an offer to "help further" while **omitting the
  data** the query tool returned — synthesis is mandatory.
- Thanking the user for "sharing" data you fetched yourself.
- Using `integral_query` (semantic search) for **superlative / ranking**
  questions — one `integral_query_spec` sort plus `limit`, or
  `integral_query_entries` with `sort_by` set to the field.
- Ranking or totalling over a truncated page of rows.
- Ranking on a custom field whose value came back null on every row, or
  naming a field by its display label instead of its schema key.
- Using `entry_type` when the user named a **track** — resolve
  `track_id` from the track name.
- Claiming a View was saved before the user blesses the
  `integral_save_view` staged card.
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
   config={filters: [{field: "status", operator: "eq", value: "open"}],
   entry_type_keys: ["deal"]})` → stage the card and **wait**; do not
   claim the view exists until blessed.

> **User:** "What's our highest-value deal?"

1. `integral_list_tracks` → resolve the Deals track id, then
   `integral_get_track_schema(track_id=<deals id>)` → the Value field's
   key is `value`.
2. `integral_query_spec(spec={resource: "entry",
   select: ["id", "title", "custom_fields.value"],
   filters: [{field: "track_id", op: "eq", value: <deals id>}],
   sort: [{field: "custom_fields.value", direction: "desc"}],
   limit: 3})` → the first row is the highest-value deal.
3. **Present:** "Your highest-value deal is *Acme Renewal* at
   $240,000." Name the winner with the value you read — never guess
   from semantic search.
