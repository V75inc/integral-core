---
name: context_correct_apply
description: "Performs a correction that context_correct has already resolved — staging the replacement fact, then superseding the old one with a link to it once the replacement's id exists. Runs in the turn after the resolution so the write starts with a full budget."
spec: jv
extends: action:integral/embedded_integral_action
requires-actions:
  - EmbeddedIntegralAction
allowed-tools:
  # NOTE: step 0's exit calls `use_skill`, which is deliberately NOT declared
  # here. It is an orchestrator loop tool, not a catalogue tool -- declaring it
  # fails skill registration outright ("declares unknown tools") and takes the
  # whole operational-layer sync down with it. The model reaches it regardless;
  # that is how it entered this skill in the first place.
  - integral_get_track_schema
  - integral_create_entry
  - integral_update_entry
  - integral_begin_batch
  - integral_commit_batch
  - integral_cancel_batch
tags:
  - personal-context
  - correction
---

# context_correct_apply — SOP

The second half of a correction. `context_correct` has already found the fact,
decided the replacement and stated both; this skill writes them.

The split exists because the two halves compete for one turn's budget.
Grounding a correction costs four tool calls against track schemas this bundle
measures in the thousands of characters; adding a two-write batch on top ran
the orchestrator into its repeat guard on six consecutive attempts. Every one
of them described the card correctly and staged none of them. A write that
starts its own turn starts with the whole budget and nothing left to discover.

**Never delete a fact.** The replacement is a new row; the old one gets
`status: superseded` and a `superseded_by` link. What was believed, and when it
stopped being believed, stays readable.

## When to use

- The previous turn resolved a correction and named the target entry, its
  track, the entry-type key and the replacement's content.
- The person confirmed a resolution that was stated but not yet written.

## When NOT to use — delegate

- **Nothing resolved yet** — `context_correct` first, and step 0 below routes
  there rather than leaving the turn stuck. This skill does not search for the
  fact being corrected; it has no query tools by design, and guessing an id
  supersedes the wrong belief.
- **New information that contradicts nothing** — `context_attend`.
- **A fact that is right but stale** — `context_promote` owns confirmation
  bumps.
- **"I don't want you tracking that"** — a settings change, not a correction.

## Grounding (read before write)

The resolution in the conversation IS the grounding. Read it there rather than
re-deriving it — a second grounding pass is what this split exists to avoid.

Call `integral_get_track_schema` only if the entry-type key is missing from the
resolution. Never pass a track's display name ("Identity") where a type key
("role") belongs: an unresolvable `entry_type` is silently swapped for the
track's default type, whose schema rejects the belief fields, and the write
fails for a reason that looks nothing like its cause.

## Procedure

**Step 0 — check the precondition, and leave if it is not met.**

This skill acts on a resolution that a previous turn stated: a target entry id,
its track, the entry-type key, and the replacement's content. Before anything
else, confirm that resolution is present in the conversation.

If it is not — a fresh thread, or a request that names no specific fact —
call `use_skill` with `personal-context__context_correct` and stop. Do not
ground, do not guess a target, and do not proceed on a partial reading.

This exit matters more than it looks. This skill has no query tools by design,
and its own rules forbid re-grounding, so a turn that enters it without a
resolution has no legal move: it cannot obtain the target and cannot act
without one. Observed live — an open-ended "supersede anything still dangling"
activated this skill on a fresh thread, and the turn burned its budget and died
on the repeat guard with nothing staged. A skill with a precondition needs a
door out of it, not just a warning in "When NOT to use".

Once the resolution IS present, a correction is TWO staged steps, in this
order, and the second depends on the first. `superseded_by` has to point at the replacement's entry id, and that id
does not exist until the create is executed — so the supersede cannot ride in
the same batch as the create that produces it.

Stage step 1, then **wait for its approval inside this turn** and continue with
step 2 once the id comes back. Do not end the turn saying you will supersede
"once you bless it": blessing a card does not start a new turn, so the
correction stops there with the replacement filed and nothing superseded —
both facts then read as live, which is the exact outcome this skill exists to
prevent.

1. `integral_begin_batch`.
2. `integral_create_entry` — the replacement. The corrected wording is the
   entry's `title`/`body`; the belief state goes in `fields`, under exactly the
   keys the track's schema declares and no others:

   ```
   fields: {confidence: "high", status: "confirmed",
            first_seen: <now>, last_confirmed: <now>,
            sources: [<observation ids from the resolution>]}
   ```

   Do not invent field keys. A key the schema does not declare (`role`,
   `organization`, `title` duplicated into fields) is refused, and
   create_entry's retry peels refused keys off one at a time and files the
   entry without them — so an invented key costs the belief fields that WERE
   valid, and the fact lands with `confidence`, `status` and `sources` all
   null. That is a fact nothing can audit and decay cannot age.

   `sources` is a RELATION to Observation entries: every value must be an
   existing entry id (`n.Entry.…`), taken from the resolution. Prose is
   rejected as an unknown entry and the retry then drops the entire field, so a
   single invented string costs every real citation with it. If the resolution
   named no ids, omit `sources` rather than describing them.
3. STEP 2, after step 1 is approved and its id is known:
   `integral_update_entry` on the old fact — `fields: {status: "superseded",
   superseded_by: "<new entry id>"}`, and nothing else. Its wording,
   `first_seen` and `sources` are the record of what was believed. Never write
   a placeholder or a title here: the value must be the id step 1 returned.
4. Repoint anything the resolution flagged as referencing the old fact.
5. One `AttentionEvent` with `kind: corrected`, naming both ids and quoting
   what the person said. This is the row that makes the chain readable later.
6. `integral_commit_batch`.
7. Confirm briefly, without defending the old belief: "Updated — Sarah is at
   Meridian, not MMG. The old entry is kept as superseded."
8. Where the corrected fact appears on a compiled page, the next
   `context_compile` regenerates it. Say so if the person is looking at it.

If a write inside the batch fails, `integral_cancel_batch` and say what broke.
A half-applied correction is worse than none: it leaves a replacement with
nothing superseded, so the person is believed twice.

## Staging discipline

The swap is two cards, because the second needs an id the first produces. Say
so on the first card — name it "step 1 of 2" and state what step 2 will do — so
the person is approving a swap they can see whole, not a create with an
invisible sequel.

**Never report the correction as applied until step 2 has landed.** Step 1
alone files a replacement and supersedes nothing, so the person is believed
twice. If the turn ends after step 1 for any reason, say plainly that the old
fact is still live and what remains.

Under `promotion_policy: always` it lands directly and the `AttentionEvent` is
the record.

Never report the correction as applied while its card is awaiting approval —
say it is staged. Do not ask whether to stage it: the card is the approval, and
asking first makes the person approve the same change twice.

The one thing that is never staged is a deletion, because there is never a
deletion.

## Forbidden patterns

- Reporting a correction as applied when only step 1 landed.
- Ending the turn after step 1 with the supersede left to a future turn that
  nothing will trigger.
- Superseding without a replacement, or pointing `superseded_by` at anything
  other than the id step 1 returned.
- Deleting, or editing the old fact's wording, `first_seen` or `sources`.
- Passing a track display name as `entry_type`.
- Re-grounding: querying for the fact again instead of using the resolution.
- Continuing past step 0 without a resolution — hand back to `context_correct`
  instead of grinding to the repeat guard.
- Arguing with the correction, or asking the person to justify it.

## Example

> **Previous turn:** "Correcting **n.Entry.7f21** (track **n.Track.p3**, entry
> type `person`): replacing it with 'Sarah — Meridian; met through MMG',
> `confidence: high`, `status: confirmed`, sources = [obs-9, obs-2, obs-4]. The
> old entry becomes superseded. Staging that now."

1. `integral_begin_batch`.
2. `integral_create_entry` — track `n.Track.p3`, `entry_type: person`,
   "Sarah — Meridian; met through MMG", `confidence: high`,
   `status: confirmed`, `sources: [obs-9, obs-2, obs-4]`.
3. `integral_update_entry` on `n.Entry.7f21` — `status: superseded`,
   `superseded_by` = the new id. Nothing else touched.
4. `AttentionEvent{kind: corrected}` quoting the correction, naming both ids.
5. `integral_commit_batch`.
6. "Staged the correction — Sarah at Meridian, met through MMG. The old entry
   stays as superseded."
