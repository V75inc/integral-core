---
name: context_correct
description: "Applies a person's correction to what the Personal Context App believes — creating the replacement fact, marking the old one superseded and linking the two — so a belief is never deleted and the history of what was believed stays readable. Triggered by an explicit correction, an edit to a compiled wiki page, or a rejection."
spec: jv
extends: action:integral/embedded_integral_action
requires-actions:
  - EmbeddedIntegralAction
allowed-tools:
  - integral_list_tracks
  - integral_get_track_schema
  - integral_query_entries
  - integral_get_related
  # READ-ONLY on purpose. This skill resolves the swap; context_correct_apply
  # performs it in the next turn. Grounding a correction costs four tool calls
  # against schemas this bundle measures in the thousands of characters, and
  # doing that plus a two-write batch in one turn ran the orchestrator into its
  # repeat guard on six consecutive attempts -- the card was described every
  # time and staged none of them.
tags:
  - personal-context
  - correction
---

# context_correct — SOP

A correction is the most valuable signal this App receives. Someone read
what it believed, disagreed, and said so. Handling it badly — silently
overwriting, or arguing — teaches them not to bother.

**Never delete a fact.** The replacement is a new row; the old one gets
`status: superseded` and a `superseded_by` link to its replacement. What
was believed, and when it stopped being believed, stays readable. A
context store that quietly rewrites its own history cannot be audited, and
one that cannot be audited cannot be trusted with anything that matters.

## When to use

- "No, actually…" / "That's wrong" / "It's not X, it's Y" about anything
  the App reported.
- The person edited a compiled sentence on a wiki page. Editing a compiled
  claim is a correction to the fact behind it, not a text edit — the next
  compile would overwrite it otherwise, which is how a person learns their
  edits do not stick.
- The person rejected a promotion card, or thumbed one down, with a reason
  that names what was wrong.
- A fact contradicts one the person has since confirmed directly.

## When NOT to use — delegate

- **New information that contradicts nothing** — `context_attend`.
- **A fact that is right but stale** — that is a confirmation bump, and
  `context_promote` owns it.
- **Editing a user-authored page section** (between `<!-- user:begin -->`
  and `<!-- user:end -->`) — those are the person's own words, preserved
  verbatim across recompiles. Not a correction; nothing to supersede.
- **"I don't want you tracking that"** — a settings change
  (`excluded_arenas`, `attention_enabled`), not a correction. Say so and
  point at the setting; superseding one fact does not stop the next one.

## Grounding (read before write)

1. `integral_list_tracks`; `integral_get_track_schema` on the track
   holding the fact — `superseded_by`, `status` and `sources` field keys
   come from the schema.
2. `integral_query_entries` to find the exact fact being corrected. Match
   on identity, not on similar wording: correcting the wrong row leaves
   the real error standing and destroys a good belief.
3. `integral_get_related` on that fact — its `sources`, and anything
   pointing at it. A superseded Person still referenced by an open
   Commitment needs the replacement linked, or the commitment is left
   pointing at history.

If the target is ambiguous, ask which one. One question at the moment of
correction is cheap; superseding the wrong fact is not, and this is not
the `question_budget` — that budget governs unprompted questions, and
this person is already talking.

## Procedure

Ground ONCE, resolve the swap, then hand off. Each grounding tool is called at
most one time; if a result is already in this turn's steps, re-read it there
rather than calling the tool again. This skill never writes.

1. Ground, per the section above.
2. Decide the replacement's content: the corrected wording, `confidence: high`
   (the person said it themselves, which is the strongest evidence available),
   `status: confirmed`, `first_seen` now, `last_confirmed` now, and `sources`.

   `sources` is a RELATION to Observation entries, so every value must be an
   entry id (`n.Entry.…`) that already exists. Two consequences:

   - The observation recording *this* correction usually does NOT exist yet —
     attention runs after the reply — so do not cite it. Cite the surviving
     sources of the old fact where they still support the new one, and
     otherwise leave `sources` empty.
   - Never put prose in it. A description like "Person correction, 2026-09-06:
     moved the review to Thursday" is rejected as an unknown entry, and
     create_entry's retry then drops the whole field — so the fact lands with
     NO citations at all, which is the one thing this App promises never to do.
3. State the swap in one short message, and include, verbatim, the four things
   the next turn cannot re-derive without re-grounding:
   - the **target entry id** being superseded,
   - its **track id**,
   - the **entry-type key** from that track's schema (`role`, `heuristic`,
     `voice`, `rhythm`) — the KEY, never the track's display name,
   - the **replacement's** title, body and belief fields.
4. Say the card is being staged. Do not ask whether to stage it — the card is
   itself the person's approval, so requesting permission to produce one asks
   them to approve the same change twice.

`context_correct_apply` performs the write from that statement.

## Staging discipline

A correction stages as two cards, in order: the replacement fact, then the
supersede that links the old fact to it. It cannot be one card, because
`superseded_by` must carry the replacement's entry id and that id does not
exist until the create is executed. `context_correct_apply` runs both and is
responsible for not stopping after the first; this skill's job is to make its
inputs unambiguous.

Under `promotion_policy: always` it lands directly and the `AttentionEvent` is
the record.

Never report a correction as applied while its card is awaiting approval. Say
it is staged.

The one thing that is never staged is a deletion, because there is never a
deletion.

## Forbidden patterns

- Deleting a fact, or editing its content in place. Supersede.
- Superseding without setting `superseded_by` — that leaves an orphaned
  belief and an unexplainable gap.
- Changing the old fact's `first_seen`, `sources` or wording.
- Arguing with the correction, or asking the person to justify it.
- Correcting a fact the person did not name, because it looked related.
- Treating an edit inside a `<!-- user:begin -->` block as a correction.
- Leaving `sources` empty on the replacement — the correction itself is a
  source and must be recorded as an observation.
- Silently repointing nothing: a superseded Person that other facts still
  reference leaves those facts pointing at history.

## Example

> **User:** "Sarah isn't at MMG, she's at Meridian. MMG is just where we
> met."

1. `integral_list_tracks`; `integral_get_track_schema` on people.
2. `integral_query_entries` for "Sarah" → one Person, `standing` "carries
   work to MMG's board", `confidence: medium`, four sources.
3. `integral_get_related` → she is counterparty on one open Commitment
   and linked to the MMG Arena.
4. Hand off, naming what the write needs:
   "Correcting **n.Entry.7f21** (track **n.Track.p3**, entry type `person`):
   replacing it with 'Sarah — Meridian; met through MMG', `confidence: high`,
   `status: confirmed`, sources = [the correction observation, plus the two
   older sources that were about Sarah herself rather than her employer]. The
   old entry becomes `status: superseded` with `superseded_by` pointing at the
   replacement, and her open Commitment repoints at it. Staging that now."
