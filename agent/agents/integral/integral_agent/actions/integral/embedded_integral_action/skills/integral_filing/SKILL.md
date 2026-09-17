---


name: integral_filing
description: "Files freeform user content into the right track and entry shape. Grounds on the workspace Content Profile via read tools before staging. Use when the user provides factual content — notes, observations, email pastes, meeting summaries — without asking clarifying questions first; stage and let the user approve the card."
spec: jv
allowed-tools:
  - integral_file_content
  - integral_list_tracks
  - integral_get_track_schema
  - integral_query_entries
requires-actions:
  - EmbeddedIntegralAction
extends: action:integral/embedded_integral_action
# Filing immediacy: pin only integral_file_content via agent.yaml pinned_tools
# (not always-active). always-active would also pin list_tracks / schema /
# query_entries schemas every turn (~greeting tax). Introspection tools still
# surface when this skill activates via use_skill / lean relevance.
always-active: false
tags:
  - integral
  - filing
  - smart_input

---

# Integral filing — SOP

## Tools vs skills — who knows what

**Filing tools** are **content-profile agnostic** and **do not classify text**.
`integral_file_content` mechanically resolves `track_id`/`track_hint` and
`type_hint`, normalizes field keys, and stages one card. It does not pick
tracks, entry types, or field values from freeform text.

**This skill** is **profile-aware through introspection**. You MUST read the
workspace's configured structure before filing — track titles, entry type names,
and field keys come from tool results **this turn**, not from memory, training
priors, or example content.

| You need | Call |
|----------|------|
| Which tracks exist | `integral_list_tracks` |
| Entry types, fields, tags for a track | `integral_get_track_schema(track_id=…)` |
| Stage one entry (one approval card) | `integral_file_content` |
| Check for duplicates | `integral_query_entries` |

`integral_file_content` is a **propose** tool — it stages a create the user
blesses in Integral. No separate execute step. Each call returns **one**
`staged_token`. Compound filing = **multiple calls in the same turn**, one per
facet.

**Required params per call:** `text`, `type_hint`, and `track_id` or
`track_hint`. Strongly recommended: `title`, `fields`.

`integral_file_content` is pinned on the lean surface every turn so filing
can start without a discovery hop. List/schema/query tools are **not**
always pinned — call `use_skill` for this skill (or `find_tool`) before
introspecting when they are absent from the turn's tool list.

## When to use this

The user provides NEW factual content: a note, observation, pasted message,
meeting summary, reminder, or informal report.

**Do not** ask "would you like me to file this?" — the staged card IS the
question.

**Confirmation = file this turn.** If the user already confirmed a plan
("go ahead", "yes", "do it"), call `integral_file_content` (or the
entries create tools) **now**. Do not re-ground on schemas and re-announce
"I'll start filing". Text without a propose call produces **no** approval
card.

Skip filing when the user asks a question, gives an explicit structured create
(`integral_entries`), or wants analytics (`integral_insights`).

## When NOT to use this

- **Acknowledgements** ("thanks", "got it") — reply briefly; do not re-file.
- **Verification** ("did it post?") — check prior tool calls or
  `integral_query_entries`; do not re-stage.
- **Workspace listing** ("what tracks do I have?") — `integral_list_tracks` or
  `integral_workspace`; do not file.
- **Prior-turn content** — if the user refers to something already staged and
  consumed, do not duplicate.
- **A new hire/employee described in plain prose** ("John Wick, he's a
  cleaner in the sanitation department, starts Monday, makes 160k") reads
  exactly like the "factual content" this skill is for, but is NOT a plain
  filing job — an installed app almost always owns a richer domain skill
  for it (e.g. HR's `onboard_employee`: batches the Employee record with
  onboarding tasks, equipment/training requests, and the record's required
  linked member account in one reviewable approval). This skill's own
  `integral_file_content` has no idea any of that exists and will file a
  bare record, then get stuck needing ad-hoc decisions (which account to
  link) it has no SOP for. Confirmed live: exactly this — a plain
  description of a new hire routed here instead of `onboard_employee`,
  produced a confusing mid-flight question with no real recovery path, and
  the follow-up action it improvised crashed outright. If the content
  describes a person joining the org (role, start date, compensation), or
  matches any other installed app's own described domain (a Pay Run, a
  Compensation Record, …), prefer that skill over filing raw content.

### Staging-resolution markers

`[SYSTEM:STAGING-RESOLVED] kind=… state=consumed|revoked summary="…"`

- `consumed` — committed; do not re-file unless asked.
- `revoked` — rejected; do not re-file unless asked.

Do not echo these markers in replies.

## Grounding — read the profile before you file

**Never assume** track names, entry type names, or field keys. Every workspace
configures its own Content Profile. Filing decisions flow from introspection:

1. **`integral_list_tracks`** — list tracks visible in the active workspace.
   Use returned `title` and `id` values only.
2. **`integral_get_track_schema`** — for each track you might file into, read
   `entry_types` (names, keys, `form_schema` fields), `tags`, and `views`.
3. **Match facets to schema** — pick `track_hint` / `type_hint` from returned
   titles and entry type names; map `fields` to returned field `key`s; shape
   `title` from whichever field the schema marks as primary display (or the
   first prominent text field).

If the user is already focused on a track (UI context / `focused_track_id`),
still call `integral_get_track_schema` for that track **this turn** unless you
already fetched it in the same turn.

When compound filing spans multiple tracks, call `integral_get_track_schema`
once per distinct target track.

## Compound filing — facet decomposition

Rich multi-subject content (long paste, email with sender + request + project
detail) **defaults to multi-facet**. Single-subject atomic notes stay
single-facet.

### Semantic facets (not entry-type names)

Facets describe *what the text contains*, not where it should go. Destination
is always chosen **after** schema introspection.

| Facet | What to extract |
|-------|-----------------|
| **Actor / identity** | Person or org identifiers, contact coordinates |
| **Intent / pipeline** | Commercial ask, quote, partnership, funding context |
| **Work / request** | Named initiative, scope, deliverables, deadlines |
| **Relationship** | Referral chain, affiliation |
| **Artifact** | Full correspondence worth preserving as-is |

When two or more facets carry independent record-worthy facts → one
`integral_file_content` call per facet, same turn.

Before staging each facet: `integral_query_entries` if the actor or initiative
name may already exist.

## Workflow

**Order is mandatory on rich content:** introspect → decompose → stage. Do not
call `integral_file_content` on a long paste until `integral_list_tracks` and
`integral_get_track_schema` have run **this turn**.

1. **Introspect** — `integral_list_tracks`; then `integral_get_track_schema`
   on candidate tracks. Build a mental map: which entry type's `form_schema`
   fits which facet (by field types and keys, not by guessing domain labels).

2. **Decompose** — split the user's content into facets (internal; do not ask
   the user to split). Never stage one combined card when multiple facets
   were detectable.

3. **Per facet**, call `integral_file_content`:
   - `text` — reporting-voice rephrase scoped to **this facet only**
   - `track_hint` or `track_id` — from `integral_list_tracks`
   - `type_hint` — **required** — exact entry type `name` or key from
     `integral_get_track_schema`
   - `title` — from schema primary field or best extracted value
   - `fields` — dict keyed by schema `field.key` values only
   - `focused_track_id` — only if genuinely known from UI context

4. **Branch on result**
   - Staged change (`_kind: staged_change`) → card is **pending approval**.
     Reply with at most one short sentence: "Staged *‹title›* in *‹track›* for
     your review." Follow `assistant_reply_hint` when present.
   - `filing_candidates` / `filing_status: unresolved` → missing hints; re-read
     schema and re-call per facet — do not claim anything was filed.

5. **Approval** — user blesses each card. **Never** say "filed", "created",
   "saved", or "posted" until `[SYSTEM:STAGING-RESOLVED] … state=consumed`.

### Reporting voice

Strip conversational openers and meta-language. Preserve substance. Use "we"
or plain declarative voice. No label prefixes ("Note:", "Update:").

| Pattern | `text` shape |
|---------|----------------|
| User gives a casual announcement | Declarative statement of the fact |
| User reports a discussion outcome | Declarative summary with who/what |

### Abstract compound pattern (no assumed profile)

After introspection returns e.g. track titles `T1`, `T2` and entry types
`E_actor`, `E_intent` with known field keys:

```
# Facet: actor/identity → entry type whose form_schema has person/contact fields
integral_file_content(
  text="<reporting-voice slice: who + coordinates only>",
  track_hint="<T1.title>",
  type_hint="<E_actor.name>",
  title="<value for primary display field>",
  fields={<schema field keys>: <extracted values>}
)

# Facet: intent/work → entry type whose form_schema fits the request
integral_file_content(
  text="<reporting-voice slice: request/scope only>",
  track_hint="<T2.title>",
  type_hint="<E_intent.name>",
  title="<short subject from facet>",
  fields={<schema field keys>: <extracted values>}
)
```

Replace every placeholder with values from **this turn's** tool output.

### Wrap-up after approvals

One line per item ("Filed *‹title›* to *‹track›*."). Do not re-stage.

### Forbidden patterns

- Saying **"Filed …"** / **"Created …"** / **"Posted …"** when the card shows
  **AWAITING APPROVAL** — use **"Staged … for your review"** instead.
- Calling `integral_file_content` without `type_hint` and without prior
  `integral_get_track_schema` this turn.
- One `integral_file_content` call on rich multi-subject paste when multiple
  facets were detectable.
- Using track or entry type names from memory, examples, or domain stereotypes.
- Copying the user's full message into every facet's `text`.
- "Would you like me to file this?" — call the tool.
- "Approving the card for you…" — only the user can bless.

## Attachments — delegate

Existing **files on an entry** (list, read their text, deliver download links)
are owned by **`integral_attachments`**, not this skill. When the user asks
what files an entry has or wants a document's content, that skill lists and
delivers them.

When filing content that *references* a file, file the **textual substance**
with `integral_file_content` into the right track/entry — the entry is the
record, the file is supporting material on it.

## Scope

Skill-driven filing of freeform content. Not explicit creates
(`integral_entries`), attachment listing/reading (`integral_attachments`),
profile authoring (`integral_profiles`), or analytics (`integral_insights`).

## Grounding rules

- Only use track ids and titles returned by tools **this turn**.
- You supply all filing params from introspection; tools validate and stage only.

## Example

> **User:** "Note: Sarah Chen from Acme called — she wants a quote for the
> website redesign by Friday."

1. `integral_list_tracks` → see which tracks exist (e.g. Contacts, Deals).
2. `integral_get_track_schema` on candidate tracks → read entry type names
   and field keys (e.g. Contacts has `contact` with `email`; Deals has
   `opportunity` with `company`, `due_date`).
3. **Decompose** into two facets — actor (Sarah/Acme) and intent (quote
   request) — then stage one card per facet:
   - `integral_file_content(text="Sarah Chen from Acme Corp.",
     track_hint="Contacts", type_hint="contact", title="Sarah Chen",
     fields={company: "Acme Corp."})`
   - `integral_file_content(text="Quote requested for website redesign,
     due Friday.", track_hint="Deals", type_hint="opportunity",
     title="Acme — website redesign quote",
     fields={due_date: "2026-07-04"})`
4. Reply briefly: "Staged *Sarah Chen* in Contacts and *Acme — website
   redesign quote* in Deals for your review." Do **not** say "filed" until
   both cards resolve consumed.
