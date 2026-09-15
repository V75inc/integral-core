---
name: context_decay
description: "Lowers salience on Personal Context observations nothing ever drew on, and archives the ones that fall below the floor, so the pending stream stays a working queue rather than an ever-growing backlog. Never touches a promoted observation or a confirmed fact."
spec: jv
extends: action:integral/embedded_integral_action
requires-actions:
  - EmbeddedIntegralAction
allowed-tools:
  - integral_list_tracks
  - integral_get_track_schema
  - integral_query_entries
  - integral_update_entry
  - integral_create_entry
tags:
  - personal-context
  - hygiene
---

# context_decay — SOP

Attention records freely, and most of what it records never becomes
anything. That is the design working — the cost of a wrong observation is a
discarded row, and the cost of a missed one is a fact never learned — but
it means the pending stream grows forever unless something drains it.

This is the drain. It is deliberately dull and deliberately timid: it
touches nothing that anybody has drawn a conclusion from.

## When to use

- The weekly run.
- The person says the stream is noisy, or asks for a clean-up.

## When NOT to use — delegate

- **Deciding what an observation means** — `context_promote`. Decay does
  not judge whether something is true, only whether anything ever used it.
- **A fact that has gone stale** — beliefs do not decay. A stale fact is
  either still believed or has been corrected (`context_correct`); silence
  is not evidence against it.
- **Removing something the person objects to** — that is a correction, or a
  settings change (`excluded_arenas`). Never a quiet archive.

## Grounding (read before write)

1. `integral_list_tracks`; `integral_get_track_schema` on `stream` —
   `salience` and `handled` field keys.
2. `integral_query_entries` on `stream` for `handled = pending` only.
   Anything already `promoted` or `discarded` has been judged and is out of
   scope.
3. `integral_query_entries` on the fact tracks for `sources` — an
   observation any fact cites is REFERENCED and is never decayed, whatever
   its age. It is the evidence for a belief; degrading it degrades the
   belief's explanation.

Step 3 is the one that matters. Skipping it silently erodes the trail behind
facts the person can currently inspect.

## Procedure

1. Ground, per the section above.
2. For each pending, unreferenced observation older than a week: lower
   `salience` by one step (a third of its current value, floored at 0).
3. Archive — `handled = archived` — anything that falls below 0.1. Archived
   is a status, never a delete: the row stays readable and a later promotion
   can still reach it if the person asks.
4. Never touch: referenced observations, anything `promoted` or `discarded`,
   anything from the last seven days, and every fact track.
5. Write one `AttentionEvent{kind: archived}` with the counts and the
   threshold used. This log is how the person sees that decay is running at
   all — silence looks identical to it being broken.

## Staging discipline

Decay writes only to the App's own `stream` track, adjusting bookkeeping
fields on rows the App itself wrote. That track is unstaged (I-PC-01), so
this runs without a card — asking somebody to approve a salience decrement
would be the clearest possible way to teach them to stop reading cards.

Nothing outside `stream` is written, ever. If decay finds itself wanting to
change a fact, it has misunderstood its job.

## Forbidden patterns

- Deleting an observation. Archive.
- Decaying an observation cited by any fact.
- Touching `promoted` or `discarded` rows.
- Touching any fact track — beliefs do not decay.
- Archiving something younger than a week because its salience started low.
  Low salience at birth is a judgement about content; decay is about
  disuse.
- Running silently. Every run writes its AttentionEvent, including
  "nothing to do".
- Raising salience. Decay only lowers; confirmation is promotion's job.

## Example

> **Weekly run.** 140 pending observations.

1. Ground: `stream` schema, `handled = pending` rows, and every fact's
   `sources`.
2. 31 are referenced by facts → untouched, whatever their age.
3. 46 are from the last seven days → untouched.
4. Of the remaining 63: 63 decrement (e.g. 0.35 → 0.23), and 12 of those
   land below 0.1 → `handled = archived`.
5. One `AttentionEvent{kind: archived}`: "Lowered salience on 63 unused
   observations, archived 12 below 0.1. 31 left alone — facts cite them."
