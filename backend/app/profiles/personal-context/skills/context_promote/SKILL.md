---
name: context_promote
description: "Turns pending Personal Context observations into typed, confidence-scored facts about the person — new beliefs, updates to existing ones, and confirmation bumps — batched into a single daily card for review under review_daily, or landed and logged under always. Marks each observation promoted or discarded so nothing is considered twice."
spec: jv
extends: action:integral/embedded_integral_action
requires-actions:
  - EmbeddedIntegralAction
allowed-tools:
  - integral_list_tracks
  - integral_get_track_schema
  - integral_query_entries
  - integral_create_entry
  - integral_update_entry
  - integral_begin_batch
  - integral_commit_batch
  - integral_cancel_batch
tags:
  - personal-context
  - promotion
---

# context_promote — SOP

Where interpretation is allowed, because this is where a person sees it.

`context_attend` writes down what was said. This reads a day of that and
proposes what it means: that a role has changed, that someone new matters,
that a promise was made. Every proposal cites the observations behind it,
so the person reviewing the card can see the evidence rather than trust
the inference.

The approval load is the design constraint. Under `review_daily` a day's
promotions are **one card**, never one card per fact. A person who is
asked to approve twelve things stops reading them.

## When to use

- The nightly schedule fires.
- The person asks for it directly ("what have you learned?", "go through
  what you've noticed").
- After an import or onboarding run has filled the stream and a first set
  of facts is wanted.

## When NOT to use — delegate

- **Recording something new** — `context_attend`. Promotion reads the
  stream; it does not add to it.
- **Answering a question about what is known** — `context_recall`, which
  reads facts and never promotes as a side effect of being asked.
- **A correction** — `context_correct`. A superseding fact carries a link
  to what it replaced; promotion does not.
- **Rewriting pages** — `context_compile`, which runs after this.
- **Lowering salience or archiving stale rows** — `context_decay`.

## Grounding (read before write)

1. `integral_list_tracks` — resolve the Personal Context tracks.
2. `integral_get_track_schema` on every track about to be written —
   the belief fields (`confidence`, `status`, `first_seen`,
   `last_confirmed`, `superseded_by`, `sources`) and each type's own
   fields come from this call.
3. `integral_query_entries` on the stream for `handled = pending`.
4. `integral_query_entries` on each fact track for anything the pending
   observations may already be about.

Step 4 decides the shape of every proposal: a new fact, an update to an
existing one, or a confirmation bump. Getting it wrong creates a duplicate
belief, and duplicate beliefs are what make a context store useless.

Read the App's `promotion_policy` setting before staging anything. It has
three values and they are not interchangeable:

| Policy | What this skill does |
|---|---|
| `review_daily` (default) | Open a batch, propose everything, commit as one card |
| `always` | Execute each proposal directly, log an `AttentionEvent` per fact |
| `never` | Propose nothing; leave observations pending; write no card |

## Procedure

1. Ground, per the section above.
2. Group the pending observations by what they are about, not by when
   they arrived. Six observations of the same person are one proposal.
3. For each group decide, and record why:
   - **new fact** — nothing existing matches
   - **update** — an existing fact is right but incomplete or has moved
   - **confirmation** — an existing fact is restated; raise
     `last_confirmed`, and raise `confidence` a step if the repeat is
     independent (a different day, a different surface)
   - **discard** — too thin to be a belief, or not about the person
4. Set confidence honestly. One mention is `low`. Repeated across days or
   surfaces is `medium`. Stated outright by the person, or confirmed by
   them on a previous card, is `high`. Confidence is not enthusiasm.
5. Set `sources` on every fact written or updated, listing the observation
   ids the proposal rests on. A fact with no sources cannot be explained
   and should not exist.
6. Set `first_seen` on creation and never touch it again; set
   `last_confirmed` on every write.
7. Under `review_daily`: `integral_begin_batch`, then one propose call per
   fact, then `integral_commit_batch` with a summary in the shape
   "N things I learned about you" — a person should be able to read the
   card and know the day's shape without opening it.
8. Mark each observation `handled = promoted` or `handled = discarded`
   with `integral_update_entry`. An observation that stays `pending`
   forever will be reconsidered every night.
9. Write one `AttentionEvent` with `kind: promoted` naming what was
   proposed, what was discarded, and why — including under `never`, where
   the honest log line is that promotion was skipped.

## Staging discipline

Facts are beliefs about a person and they are staged. Silence is consent
for a belief to stand once the person has seen it on a card; it is never
consent for the belief to appear without one.

Under `review_daily` the batch is the unit: `integral_begin_batch` opens
it, each propose call accumulates into it rather than minting its own
token, and `integral_commit_batch` mints the single card. If the batch
cannot be committed, `integral_cancel_batch` and leave every observation
`pending` — a half-promoted day is worse than a skipped one.

Under `always` the person has granted standing permission for facts to
land without a card. Log every one in the Attention Log; that log is the
whole of their visibility into what changed.

Never say "learned", "recorded" or "updated" about a fact whose card is
still awaiting approval. It is *proposed* until the card resolves
consumed.

Observation `handled` flags are bookkeeping inside the App's own stream,
not changes to the person's substrate, and are written directly.

## Forbidden patterns

- One card per fact under `review_daily`. The batch exists so the person
  reviews once.
- Promoting under `never`.
- Writing a fact with an empty `sources` list.
- Creating a second fact where one already exists — the dedupe query in
  grounding is what prevents it.
- Deleting or overwriting a fact that turned out to be wrong. Superseding
  is `context_correct`'s job and it keeps the old row.
- Raising `confidence` on a repeat that is the same observation seen twice
  rather than an independent confirmation.
- Changing `first_seen` on an existing fact.
- Leaving observations `pending` after considering them.
- Inventing an Arena or a Person to hang a commitment on. Propose the
  commitment without the link and let a later day resolve it.
- Claiming a card was approved. Only the person blesses.

## Example

> **Nightly run.** Eleven pending observations.

1. Ground: tracks, schemas, `handled = pending` on stream, existing facts
   on identity / arenas / people / commitments. `promotion_policy` is
   `review_daily`.
2. Group: four are about Sarah, three about the MMG engagement, two about
   a promise made on Tuesday, one about working late on Thursdays, one is
   a build failure that is not about the person at all.
3. Decide: Sarah exists → confirmation with a sharpened `standing`; MMG
   exists → update, `arena_status` moves to `active`; the promise is new →
   a Commitment with `direction: made`, `horizon` Friday, counterparty
   Sarah; Thursday evenings is one observation only → a Rhythm at `low`
   confidence; the build failure → discard.
4. `integral_begin_batch`, then four propose calls, each carrying its
   `sources` ids, then `integral_commit_batch(summary="4 things I learned
   about you today")`.
5. `integral_update_entry` on all eleven observations — ten `promoted`,
   one `discarded`.
6. One `AttentionEvent{kind: promoted}`: "Proposed 4 (1 new commitment,
   1 new rhythm, 2 confirmations), discarded 1 as not about you", with
   every id in `refs`.
7. Reply, if anyone is listening: "Staged 4 things I learned about you
   today for your review." Not "learned" — staged.
