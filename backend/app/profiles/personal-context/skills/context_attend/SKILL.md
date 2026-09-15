---
name: context_attend
description: "Notices what a just-finished interaction or a just-saved entry reveals about the person and records it as a verbatim observation in the Personal Context stream, with provenance and salience. Dedupes against existing facts first and bumps confirmation rather than duplicating. Never interprets, never asks, never writes a fact."
spec: jv
extends: action:integral/embedded_integral_action
requires-actions:
  - EmbeddedIntegralAction
allowed-tools:
  - integral_list_tracks
  - integral_get_track_schema
  - integral_query_entries
  - integral_create_entry
tags:
  - personal-context
  - attention
---

# context_attend — SOP

The door of the Personal Context App. It runs after a turn has already been
answered, or when an entry has just been saved, and its whole job is to
write down what was there — not what it means.

Two rules govern everything below.

**Verbatim at the door.** An observation carries the text as it was
written. Interpretation happens at promotion, in `context_promote`, where
a person can review it. A paraphrase recorded here is a paraphrase nobody
ever gets to check.

**Observe freely, promote deliberately.** Observations are cheap. Getting
one wrong costs a discarded row; missing one costs a fact that is never
learned. Lean toward recording.

## When to use

- A resident turn has just closed and the utterance, the reply, the tools
  called, or the staged outcomes said something about the person: what they
  are working on, who they work with, what they have promised, how they
  decide, how they sound, when they work.
- An entry was created or updated in any app and its content reveals the
  same.
- An import or connector delivered material about the person for the first
  time.

Salience is a judgement about how much a line says about the *person*, not
how important the work is. "Ship the migration by Friday" is a commitment
and scores high. "The build is red" is about the build.

## When NOT to use — delegate

- **Turning observations into facts** — `context_promote` owns that, and
  owns the card the person reviews.
- **Answering what the App knows** — `context_recall`.
- **A correction** ("no, actually…") — `context_correct`. A correction is
  not an observation of a new fact; it supersedes an old one.
- **Rewriting the wiki** — `context_compile`.
- **Anything about the agent's own working memory** — that is
  `agent-scratch`, a different App with a different subject. Scratch is
  about the work; this stream is about the person.

Never asks the person anything. `context_gap_review` owns questions, and
it has a daily budget precisely so attention cannot become an interview.

## Grounding (read before write)

Read before writing, every time, in this order:

1. `integral_list_tracks` — resolve the Personal Context tracks in the
   personal workspace. Track ids come from this call, never from memory.
2. `integral_get_track_schema` on the stream track — read the observation
   field keys (`surface`, `arena`, `workspace_ref`, `interaction_ref`,
   `entry_ref`, `excerpt`, `salience`, `handled`) rather than assuming
   them.
3. `integral_query_entries` against the fact tracks (identity, arenas,
   people, commitments) for anything the candidate already matches.

Step 3 is the dedupe gate and it is not optional. A stream full of the
same observation restated forty times is a stream nobody can promote from.

**On a match:** do not write a second observation of the same thing.
Write one observation whose title says it is a repeat and whose `excerpt`
carries the new wording, so `context_promote` can raise the matching
fact's `last_confirmed` and confidence. Never edit the fact here —
attention writes to the stream and to nothing else.

**On no match:** write the observation.

## Procedure

1. Ground, per the section above.
2. Split what was said into candidates. One observation per thing
   observed: two facts in one utterance are two rows, the same way
   `integral_filing` decomposes a paste into facets.
3. Skip anything that is not about the person — task content, code, build
   output, the agent's own reasoning.
4. For each surviving candidate, `integral_create_entry` into the stream
   track with:
   - `title` — the one-line gist, in reporting voice
   - `body` and `excerpt` — the source text, verbatim
   - `surface` — `chat`, `entry`, `staging`, `connector` or `import`
   - `interaction_ref` / `entry_ref` — whichever applies; this is the
     thread back to where it came from and an observation without one
     cannot be explained later
   - `workspace_ref` — where it was observed, which is not necessarily
     where the observation is stored
   - `arena` — only when an existing Arena clearly matches; leave empty
     rather than guessing, and let promotion resolve it
   - `salience` — 0.0 to 1.0
   - `handled` — always `pending`
5. Write exactly one `AttentionEvent` with `kind: observed` into the
   attention track, summarising how many observations were recorded and
   naming their ids in `refs`. One event per run, not one per observation.
6. Say nothing to the person. This skill produces no reply.

## Staging discipline

Observations are working memory about the person, not changes to their
substrate. They land in the stream track unstaged, the same posture
`agent-scratch` holds for the resident's working notes, and they are
visible immediately in the Stream feed and the Attention Log.

Nothing else in this skill writes anywhere. Facts are staged, and
`context_promote` stages them — under `review_daily`, as one batch card a
day rather than a card per fact.

Attention runs after the reply has been sent. It must never hold a turn
open, never emit a card mid-conversation, and never inject anything into
the next turn. It records; it does not steer.

## Forbidden patterns

- Writing a fact — a Role, Person, Arena, Commitment, Heuristic, Voice,
  Rhythm or Ambition. Attention writes observations. Only promotion writes
  facts.
- Paraphrasing into `excerpt`. The excerpt is the evidence.
- Writing without `integral_query_entries` having run this turn.
- Editing an existing fact's `last_confirmed` or `confidence` directly.
  Record the repeat as an observation and let promotion do it.
- Asking the person anything, or replying at all.
- Observing entries in the Personal Context App itself, or in
  `agent-scratch`. That is a feedback loop: the App would observe its own
  observations forever.
- Observing anything in an arena listed in `excluded_arenas`, or observing
  at all when `attention_enabled` is false.
- Recording task content, code or build output because it appeared in the
  same message as something about the person.
- Inferring a person's standing, an arena's kind, or a commitment's
  horizon from one mention. Record what was said; confidence is set at
  promotion.

## Example

> **Turn:** "Can you get the MMG deck to Sarah before Friday? She's the one
> who has to take it to their board."

1. `integral_list_tracks` → the Personal Context tracks and their ids.
2. `integral_get_track_schema` on stream → observation field keys.
3. `integral_query_entries` on people for "Sarah", on arenas for "MMG",
   on commitments for the deck → Sarah exists as a Person; MMG exists as
   an Arena; the commitment does not.
4. Three candidates, two of them repeats:
   - commitment (new) — `integral_create_entry(title="Owes Sarah the MMG
     deck before Friday", excerpt="<the utterance, verbatim>",
     surface="chat", interaction_ref="<id>", salience=0.8,
     handled="pending", arena="<MMG arena id>")`
   - Sarah (repeat) — one observation titled "Sarah again, as the person
     who carries work to MMG's board", excerpt verbatim, so promotion can
     raise her `last_confirmed` and sharpen `standing`
   - MMG (repeat) — recorded the same way
5. One `AttentionEvent{kind: observed}`: "Noticed 3 things in this turn —
   1 new commitment, 2 confirmations", with all three ids in `refs`.
6. No reply.
