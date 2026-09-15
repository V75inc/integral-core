---
name: context_gap_review
description: "Asks the person the single highest-value question the Personal Context App cannot answer by inference — capped by their question budget, at most one thing at a time, and only when the answer would change several beliefs rather than fill one blank."
spec: jv
extends: action:integral/embedded_integral_action
requires-actions:
  - EmbeddedIntegralAction
allowed-tools:
  - integral_list_tracks
  - integral_get_track_schema
  - integral_query_entries
  - integral_get_related
  - integral_ask_user
  - integral_create_entry
tags:
  - personal-context
  - questions
---

# context_gap_review — SOP

The one place this App is allowed to interrupt.

Everything else observes, infers and waits. This asks — and the budget
exists because an App that pays attention all day and then asks about it
becomes an interview. One question, when it is worth one.

The bar: **ask only what would change several beliefs, and only what
watching cannot settle.** "Is MMG a V75 engagement or its own arena?"
reshapes an arena, the people in it and the commitments under it, and no
amount of observation resolves it. "What is Sarah's job title?" fills one
blank and will probably arrive on its own.

## When to use

- The daily run, when the budget allows.
- The person asks what the App is unsure about.

## When NOT to use — delegate

- **Anything a read tool answers** — query first. Asking about something
  already recorded is the fastest way to look like it has not been paying
  attention.
- **Confirming a write** — staged changes already gate those.
- **A correction the person just made** — accept it (`context_correct`);
  do not interrogate it.
- **Filling a page's empty section** — `context_compile` leaves it empty. An
  honest gap is not a reason to interrupt.

## Grounding (read before write)

1. `integral_list_tracks`; `integral_get_track_schema` on the fact tracks.
2. `integral_query_entries` for the gaps worth caring about:
   - Arenas with no `my_role`, or `confidence: low` after repeated sightings
   - People with no `standing`
   - Commitments with no `counterparty` or no `horizon`
   - Two facts that appear to describe the same thing
3. `integral_get_related` to check whether the answer is already implied by
   something linked. Very often it is.
4. `integral_query_entries` on `attention` for `kind = asked` — what was
   already asked, and when. **Never ask the same question twice.** A
   question the person ignored is an answer: they did not think it
   mattered.
5. Read `question_budget` (0–3/day, default 1) and count today's `asked`
   events. At budget: stop. Do not save it up, and do not spend tomorrow's.

## Procedure

1. Ground, per the section above.
2. Rank the gaps by how much would change if answered. Count the facts
   affected, not the fields.
3. If the best candidate would change fewer than two facts, **ask nothing**
   and log that. A quiet day is a good outcome.
4. Otherwise ask exactly one, with `integral_ask_user`: one question, 2–6
   concrete options drawn from what has actually been observed, plus room
   to answer freely. Options are how the person answers in three seconds
   instead of three sentences.
5. Say what hangs on it — one clause, not a paragraph: "asking because it
   changes how I file the MMG work and who I attach to it."
6. Write one `AttentionEvent{kind: asked}` with the question and the gap it
   targets, whether or not it gets answered.
7. The answer arrives as an ordinary turn. `context_attend` observes it and
   `context_promote` promotes it. This skill does not write the fact itself.

## Staging discipline

A question is not a change, so nothing stages. `integral_ask_user` mints no
StagedChange — it records a pending question on the thread.

The answer becomes an observation, and the fact it supports is staged like
every other fact. Asking never shortcuts the promotion path: an answer given
directly is strong evidence, not an approved belief.

## Forbidden patterns

- More than one question in a run, or exceeding `question_budget`.
- Asking anything a query would answer.
- Re-asking something in `attention` as `kind = asked`, including rephrased.
- Batching several gaps into one multi-part question. Two questions wearing
  one question mark are two questions.
- Asking when `attention_enabled` is false — somebody who switched
  attention off did not ask to be interviewed instead.
- Writing the fact from the answer directly. Observe, then promote.
- Explaining at length why the question matters. One clause.

## Example

> **Daily run.** `question_budget: 1`, nothing asked today.

1. Ground: fact tracks, then `attention` for prior `asked` events.
2. Gaps found: MMG has no `my_role` and 11 observations; two Person rows
   may be the same "Sarah"; a Commitment has no `horizon`.
3. Rank: the MMG role changes the Arena, the 3 commitments under it and how
   2 people attach — the widest. The duplicate Sarah is next. The missing
   horizon changes one fact.
4. `integral_ask_user`: "Is MMG its own arena, or an engagement under V75?"
   — options *Its own arena* / *An engagement under V75* / *Something else*
   — with "asking because it changes how I file the MMG work and who I
   attach to it."
5. One `AttentionEvent{kind: asked}` naming the arena and the gap.
6. Stop. The duplicate Sarah waits for tomorrow.
