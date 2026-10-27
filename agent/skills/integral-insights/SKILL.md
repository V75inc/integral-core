---
name: integral-insights
description: Read workflow instructions. Load with load_capability; this skill name is never a callable tool. Search existing Integral records and current workspace state to answer one-time questions. Find whether information is already filed, search entries by meaning, explain what could hold up work, dependencies, risks, fallback options and stored intra-workspace relationships; summarize, count, rank, compare, or break down current data; optionally save a query as a View. For a new operational app, use integral-scaffold. For automatic periodic reports or repeated checks, use integral-scheduling; for review deliverables, use integral-review.
allowed-tools: integral_list_views integral_open_view integral_list_workspace_tools integral_call_workspace_tool integral_get_index_status integral_get_graph_context integral_describe_capabilities integral_governed_query integral_query_spec integral_plan_query integral_query integral_query_entries integral_count_entries integral_count_workspace_entries integral_aggregate integral_activity_digest integral_get_digest integral_save_view integral_list_apps integral_list_tracks integral_get_track_schema integral_resolve_entry integral_describe_substrate integral_get_feed integral_list_notifications integral_mark_notification_read integral_search_cross_track integral_list_tags
---

# Integral insights — SOP

## Start after loading

If the user also asks for automatic future updates, this read is only the
immediate part. Discover and load `integral-scheduling` for the cadence and stage
an agent routine that repeats the current read. Do not infer that scheduling is
unavailable because it is not in this skill's tools, or stop after the first
count. Claim a limitation only when current capability or execution evidence
establishes it. The scheduled runs must fetch fresh facts, not echo task text.

For records in an installed App, first resolve its App ID from current scoped
context or `integral_list_apps`. Call
`integral_list_workspace_tools(app_id=<verified App ID>, read_only=true)` and use
the matching read-only tool via `integral_call_workspace_tool`, with the exact
returned key and input schema. These registered tool keys are not declared
query keys; never pass them to `integral_governed_query`. If none fits, inspect
`integral_describe_capabilities` for that App's declared queries. Never invent
a key or use another App's query. A generic read refused with `app_domain`
did not read records: follow this discovery path once rather than repeating
generic reads. If neither route is available, explain the limitation briefly
and answer any other parts already supported by the results.

The catalogue is paged. If it has more results, follow `next_offset` only when
the current page lacks a matching read. Request `tool_key` for one known schema.
When an `app_domain` refusal includes `recovery.read_tools`, use those exact
keys and schemas rather than rediscovering the whole workspace catalogue.

For a question about saved prose, wiki pages or guidance, use `integral_query`
with `mode="hybrid"` (or semantic for paraphrases), `top_n=3` and
`context_tokens=600`. Narrow to the known `app:<id>` or `track:<id>`.
Read its returned `passages`, answer briefly and cite `action_url` exactly as
returned. Keep relative paths relative; never invent or prepend a deployment
host (including placeholder domains). When the
passages answer the question, stop reading; do not fetch whole records again.
Use structured reads for exact fields, rankings or record relationships.
An empty or limited index is not proof that information does not exist. If
needed, use a focused keyword fallback and describe the remaining gap.

Explain search readiness in everyday language: whether search is ready and
how many records are up to date or still waiting. Keep this brief. Include
model names, vector dimensions or internal identifiers only when requested.


Read native format v2 correctly before composing an answer:
- Each `nodes[]` item is one record. Its **`context` contains actual values**.
  For a declared path `custom_fields.<key>`, read
  `node.context["custom_fields.<key>"]` from that same node.
- **`fields` is metadata**, not the value store. Its `evidence.verification`
  is a verification note; never substitute it for operational state.
- Pair each label, state, date and quantity within its own node. Do not zip
  separate lists, sort dates separately, or borrow an adjacent node's deadline.
  Make a small record-by-record table: Record | operational state | deadline |
  quantity/unit | verification note. Copy each cell from that row's node context
  and metadata. Then explain the edges separately.
- A deadline is not an observation date. Null quantity means unknown for that
  record; it does not mean another prerequisite lacks a reservation. Apply
  qualification/reservation conditions only to the relation they describe.

For a dated decision question, start your final answer with a Markdown table.
This visible table is REQUIRED, not an internal preparation step:

| Record | Recorded state | Deadline | Quantity / evidence |
| --- | --- | --- | --- |
| <node.label> | <that node's operational value> | <that node's deadline value> | <that node's quantity/unit and verification note> |

Include the subject and each relevant prerequisite/fallback as separate rows.
Before emitting a date, match the row label to its node and copy that node's
`context` deadline value exactly. Never take a date from another node or
observation field. If a value is absent, write unknown. Follow the table with
two short sentences explaining mitigation and remaining evidence gaps.
The final answer must cover this compact structure:
- **Prerequisites:** each relevant record's name, recorded operational state,
  and its returned deadline. Include dates; naming the prerequisites alone
  leaves the timing question unanswered.
- **Fallback:** returned candidate quantity and units, qualification/reservation
  state, and returned deadline; explain which prerequisite it could mitigate
  and which separate prerequisite remains.
- **Evidence gaps:** identify unconfirmed commitments or missing facts. Attribute
  dated notes as recorded evidence. Do not substitute generic lifecycle status
  for operational state or a verification note for its field's actual value.
Populate these from the current capture. Omit a quantity/date only when it was
not returned; say unknown when its selected value is null or missing. Do not
omit a returned date or proposed quantity merely because it is unconfirmed:
report it as a dated proposal, with the qualification/reservation caveat.

Your next action is a READ FUNCTION, never `integral-insights`:
- Known record ID: `integral_resolve_entry(entry_id="<verified id>")`.
- Named subject in an installed App: use the registered read-tool route above.
- Other named subject: `integral_query_entries(query="<focused subject phrase>", limit=5)`.
- Concept: `integral_query(query="<focused concept>", top_n=5)`.
Use the returned entry ID for the relationship capture below. If a prior call
reported "Unknown tool name" for this skill, do not repeat that call. The skill
is already loaded; call the actual read function. Search for its exact name
only if that function's schema is absent.

This skill is instructions, not a callable tool. Do not invoke the skill's
name as a tool. Loading it discloses the primary read schemas:
`integral_query_entries`, `integral_query`, and `integral_get_graph_context`.
Use those actual tools directly. If a required schema is absent, call
`search_capabilities` for that read, then use the returned name and arguments.
For a named subject, use `integral_query_entries`; for a concept, use
`integral_query`. Other instruments remain discoverable as needed.
Do not call this skill's name, promise to search, or ask for records before
attempting the authorized read.

## Dependency and decision questions

For blockers, prerequisites, connected risks, or fallback options:

1. **Find the subject.** Use a focused phrase from the question in
   `integral_query_entries` (cross-track keyword search) for a named record,
   or `integral_query` for a concept. Search only the active workspace.
   Resolve IDs from returned records; never ask the user for an internal ID.
   For `integral_query_entries`, use the matching entry's `id`. For
   `integral_query`, use the matching result's `entry_id`, not its `track_id`.
   A track ID identifies the containing collection, not the subject entry.
   If one record clearly matches, proceed. Ask for clarification only after
   the read finds no plausible subject or multiple genuinely ambiguous ones.
2. **Read its relationships.** Call `integral_get_graph_context`
   with that returned `root_id`, `view="ontology"`, `detail="recorded"`,
   and `max_depth=3`, `max_bytes=49152`.
   For entry dependency/reference questions use `edge_types=["REFERENCES"]`.
   If the schema is not yet disclosed, search for relationship context.
   Finding the subject is an intermediate result, not the complete answer:
   text search alone cannot establish the connected dependency paths.
   REFERENCES connects entries to entries. If you accidentally captured a
   container and received only that container with no edges, correct the root
   to the subject's already-returned entry ID and capture again. Do not treat
   a container-only capture as evidence that the entry has no dependencies.
3. **Answer the decision question.** Explain the relevant readable named
   prerequisites and how the fallback connects. Use returned relationship
   metadata, declared relation definitions, recorded fields and paths.
   Use operational state, due dates and recorded verification when returned.
   `status=active` is substrate lifecycle, not completion. Field states
   distinguish present, null, missing and truncated; missing is unknown.
   Evidence is recorded, not independently verified. Check observation dates
   before treating a fact as current. For a dated decision, include the relevant
   prerequisite and fallback deadlines returned by the capture. If fallback
   capacity is indicative or qualification/reservation is pending, say so
   explicitly; a candidate quantity is not committed capacity. Explain which
   prerequisite it could mitigate and which separate prerequisite still applies.
   Do not invent an exclusive supplier requirement or declare a pending task
   already overdue without a returned deadline supporting that claim. Unresolved/ambiguous meanings cannot
   establish a prerequisite merely from its field name. Describe a stored link as evidence, not proof that a
   blocker has happened or that a mitigation guarantees success. Missing
   completion/capacity evidence means readiness is unconfirmed. Distinguish
   verified stored relationships from your interpretation in plain language.
   Respect incomplete captures and current access; do not infer hidden facts,
   traverse another workspace, or expose unrelated readable records.

Keep tool names and internal identifiers out of the user's explanation unless
requested. This is a read workflow; do not create or change any records.
Answer in at most 150 words: name the stored prerequisites, explain which one
the fallback addresses, report the useful recorded detail, and identify the
remaining evidence gaps. Do not issue a blanket completion/capacity caveat
when specific recorded evidence answers it; attribute that evidence and its date. Do not imply the fallback solves an unrelated prerequisite.
Cite captured labels as plain text unless the result supplies a canonical
`url`; then copy that URL exactly. Never construct a link from a node ID.

Example: "What could delay opening our new site, and how would temporary
space help?" Discover the entry search, search for "new site", and select
the matching returned entry. Then discover relationship context and capture
that entry's REFERENCES paths. Explain only the captured prerequisites and
the path connecting temporary space to a prerequisite. Search matches that
are outside those paths are not established dependencies. Do not turn a
fallback into a guarantee, or claim it solves an unrelated prerequisite.

## When to use

Ordinary questions such as "What could hold up our next delivery?", "What
needs to happen first?", or "How would a fallback help?" ask for workspace
insights. The user does not need to name a tool, record ID, graph, or ontology.
Find the relevant readable records yourself before requesting more context.
Do not claim that you lack workspace access without first attempting the
appropriate authorized read.

The user is asking about **state** of their substrate — "what's
overdue," "how many open bugs," "what happened last week,"
"compare Marketing vs Engineering this month," "who owns the budget
work." That's the **insight mode**.

This is distinct from individual entry reads in `integral-entries`:

- **`integral_query_entries`** / **`integral_resolve_entry`** (in
  `integral-entries`) — fetch specific known entries inside a known
  track. Use when the user references a known target.
- **`integral_query`** — unified hybrid retrieval (graph / semantic /
  hybrid) across readable entries in the active workspace. This is the primary
  open-ended retrieval tool: use it for "find anything about X,"
  cross-track concept search, or "what relates to Y" when the user
  isn't naming one track.
- **`integral_get_graph_context`** — bounded relationship, hierarchy, or
  ontology evidence for decision making. Start with the workspace default
  or a known readable entry/track/app root. Use returned paths and declared
  meanings to explain connections; stored edges do not establish causation.
  Check completeness, radius, and expansion points before drawing conclusions.
  Expand only a relevant included point with a fresh call. Private records and
  installed-App entry records are excluded, so absence is not proof of absence.
  All evidence stays inside the active workspace under the current user's
  permissions. Never combine workspace captures to reconstruct an excluded
  relationship, even when the user can read both endpoints.
  This is on-demand context, not a workspace-wide snapshot or exact count.
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
- **`integral_count_workspace_entries`** — the exact content-free inventory
  total across the current workspace, including installed Apps, limited to
  Entries the caller can read. Use for "how many entries are in this workspace".
  No filters or per-App domain breakdowns; use declared App queries for those.
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

For dependency and decision questions, follow the workflow above through the
relationship capture before answering. For simple retrieval, choose the
simplest read that answers the request, execute it, and present its result.

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

2. **Stop when the result is authoritative for the requested outcome.**
   For a dependency or fallback question, retrieval finds the subject;
   continue to its relationship capture as specified above. For simple
   retrieval, a successful, non-degraded
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

   - Explain known relationships or structure before a decision →
     integral_get_graph_context. Cite readable names and supporting paths;
     separate stored facts from your inference. Respect truncation and App
     boundaries. Use QuerySpec or declared App queries for exact business facts.
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

- Cite entry titles and counts the API actually returned this
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

## Show the relevant place

Use `integral_open_view` when opening the relevant App, collection, record or
saved view helps the user. Resolve actual accessible IDs first; for a specific
saved collection view, discover it with `integral_list_views` rather than guessing.
Keep the reply short. Navigation does not create or change data and needs no
approval. Do not move the user on every answer or claim navigation succeeded when
the tool says no browser or access is available.
