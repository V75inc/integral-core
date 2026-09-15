---
name: context_onboard
description: "The first fitting of the Personal Context App — one conversation plus whatever the person already has (personal-crm contacts, knowledge-base pages, a pasted memory export) turned into a first set of proposed facts as a single card, so the App starts with a shape instead of an empty wiki."
spec: jv
extends: action:integral/embedded_integral_action
requires-actions:
  - EmbeddedIntegralAction
allowed-tools:
  - integral_list_apps
  - integral_list_tracks
  - integral_get_track_schema
  - integral_query_entries
  - integral_ask_user
  - integral_create_entry
  - integral_begin_batch
  - integral_commit_batch
  - integral_cancel_batch
tags:
  - personal-context
  - onboarding
---

# context_onboard — SOP

An empty wiki teaches nothing. The App is far more convincing on day one if
it already knows the shape of somebody's world — and most of that shape is
already sitting in their workspace.

So: import first, ask second, propose once. The person should feel
recognised, not interviewed.

## When to use

- First connect, when the App has no facts.
- The person asks to import something, or offers a memory export from
  another assistant.
- After a long dormancy, when the App was installed but never used.

## When NOT to use — delegate

- **Routine promotion** — `context_promote`. Onboarding is the first pass,
  not the nightly one.
- **A gap in an established context** — `context_gap_review`, which is
  budgeted precisely so it cannot become this.
- **Re-importing** — check for existing facts first. Running this twice
  should propose almost nothing the second time.
- **Correcting what onboarding got wrong** — `context_correct`. The first
  set of facts is provisional by construction and will be corrected; that
  is normal, not a failure.

## Grounding (read before write)

1. `integral_list_apps` — what the person already has. Import from an App
   only when it is installed; never assume.
2. `integral_list_tracks`; `integral_get_track_schema` on both the sources
   and this App's tracks.
3. `integral_query_entries` on this App's fact tracks. **If facts exist,
   this is not a first fitting** — propose only what is genuinely new, or
   hand off to `context_promote`.
4. Read the sources that are actually there:
   - a personal CRM's contacts → candidate People
   - a knowledge base's pages → candidate Arenas, Heuristics, Voice
   - workspaces and Apps the person owns → candidate Arenas
   - anything they pasted → whatever it carries

Imported material is evidence, not belief. Every import lands as an
observation in `stream` first, and the facts are proposed FROM those
observations, so the trail from a day-one fact back to where it came from is
identical to every other fact's.

## Procedure

1. Ground, per the section above.
2. Import: write one observation per source item, `surface: import`, excerpt
   verbatim, `handled: pending`. Do not interpret yet.
3. Ask at most **three** questions, in one exchange, and only what the
   import cannot answer. Good ones are structural: what arenas they work
   across, who they work with most, what they are trying to bring about.
   Skip any the import already answered.
4. Propose the first facts from the observations, in ONE batch:
   `integral_begin_batch` … `integral_commit_batch`, summarised as
   "Here is what I think I know about you — N things."
5. Confidence is honest and therefore mostly low. Something the person said
   in step 3 is `high`. Something inferred from an imported contact list is
   `low`. Day-one confidence is not a measure of enthusiasm.
6. Set `sources` on every fact. A day-one fact with no trail is exactly the
   fact somebody will later ask about.
7. Write one `AttentionEvent{kind: promoted}` recording what was imported,
   what was asked, and what was proposed.
8. Say what happens next in one sentence: it keeps noticing, it proposes
   once a day, and they can correct anything by saying so.

## Staging discipline

One card. Onboarding proposes a person's whole starting shape, and it does
it as a single batch they accept or reject as a whole — twelve cards on
first connect is how somebody decides this App is exhausting.

Imported observations land unstaged in `stream` (I-PC-01) like every other
observation. The facts stage.

Under `promotion_policy: always` the facts land directly, but onboarding
still writes the AttentionEvent — day one is exactly when a person needs to
see the App's reasoning.

If the batch cannot be committed, `integral_cancel_batch` and leave the
observations pending. A half-onboarded context is worse than an empty one,
because the person cannot tell which half is missing.

## Forbidden patterns

- More than three questions, or asking one at a time across several turns.
- Asking anything the import already answered.
- One card per fact.
- Importing an App that is not installed, or inventing what it would have
  contained.
- Writing facts directly from imported rows without observations behind
  them — that produces day-one facts nobody can explain.
- `confidence: high` on anything inferred rather than stated.
- Copying a CRM's contact data into Person rows wholesale. Link with
  `crm_contact_ref` and record what the person's relationship IS; the CRM
  keeps the coordinates.
- Running a full onboarding over an App that already holds facts.

## Example

> **First connect.** `personal-crm` and `personal-knowledge-base` are
> installed; the App has no facts.

1. `integral_list_apps` → both present. `integral_query_entries` on this
   App's fact tracks → empty, so this is a first fitting.
2. Import: 34 CRM contacts and 12 KB pages → 46 observations,
   `surface: import`, `handled: pending`, excerpts verbatim.
3. The import suggests three arenas but says nothing about what the person
   is trying to bring about, and nothing about how they decide. Two
   questions, one exchange: "Which of these are you actually working in
   right now?" (options from the three) and "What are you trying to build
   this year?"
4. `integral_begin_batch` → 3 Arenas (`medium` — the import and their answer
   agree), 8 People (`low` — inferred from contact frequency), 1 Ambition
   (`high` — they said it), 2 Heuristics (`low` — inferred from KB pages) →
   `integral_commit_batch(summary="Here is what I think I know about you —
   14 things")`.
5. One `AttentionEvent{kind: promoted}`: 46 imported, 2 asked, 14 proposed.
6. "I will keep noticing as you work, propose what I have learned once a
   day, and change anything you tell me is wrong."
