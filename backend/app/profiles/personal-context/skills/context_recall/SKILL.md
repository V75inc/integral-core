---
name: context_recall
description: "Answers what the Personal Context App believes about the person — roles, arenas, people, commitments, heuristics, voice, rhythms — from typed facts only, always with the confidence, when it was last confirmed, and the observations behind it. Says plainly when nothing is known rather than inferring."
spec: jv
extends: action:integral/embedded_integral_action
requires-actions:
  - EmbeddedIntegralAction
allowed-tools:
  - integral_list_tracks
  - integral_get_track_schema
  - integral_query_entries
  - integral_get_related
tags:
  - personal-context
  - recall
---

# context_recall — SOP

Reads. Writes nothing, ever.

The App's credibility rests on this skill being boring: it reports what is
believed, how sure it is, and where that came from. A recall that quietly
fills a gap with a plausible inference destroys the distinction between
what the App was told and what it made up — which is the distinction the
whole App exists to keep.

## When to use

- "What do you know about me?" / "…about Sarah?" / "…about MMG?"
- "What have I committed to this week?"
- "How do I usually decide this?" / "How do I talk to their board?"
- Any question whose answer is a belief this App holds.
- Another skill needs the person's context and asks for it.

## When NOT to use — delegate

- **New information arrived** — `context_attend` records it.
- **The person says a belief is wrong** — `context_correct`, which
  supersedes it. Recall must not quietly rewrite a fact it just reported.
- **Turning pending observations into facts** — `context_promote`. Being
  asked a question is not a reason to promote.
- **General workspace queries** — how many entries a track holds, what
  apps exist — belong to the core Integral skills. This one answers about
  the *person*.

## Grounding (read before write)

Read-only throughout, but the ordering still matters.

1. `integral_list_tracks` — resolve the Personal Context tracks.
2. `integral_get_track_schema` on the tracks about to be read, so field
   keys come from the schema.
3. `integral_query_entries` on the fact tracks, filtered to
   `status` in (`provisional`, `confirmed`). A superseded or archived fact
   is history, not a current belief, and is quoted only when the question
   is about history.
4. `integral_get_related` to walk a fact's `sources` back to the
   observations, and its `arena` / `counterparty` / `register` links out
   to neighbours.

Facts only. The stream is evidence, not belief: an observation that was
never promoted was not judged worth believing, and reporting it as though
it were is how an unreviewed guess becomes something the person thinks
they said.

The one exception is an explicit question about the raw record — "what did
you notice yesterday?" — where the stream *is* the subject. Say so
plainly: "not promoted yet, so I have not judged it."

## Procedure

1. Ground, per the section above.
2. Answer from the facts found. Every claim carries three things:
   - **confidence** — `low`, `medium` or `high`, in words
   - **last confirmed** — when, not just that it was
   - **sources** — how many observations, and what they were, when asked
3. Prefer the person's own words. A Voice register or a Heuristic quoted
   verbatim from its sources is worth more than a summary of it.
4. Say what is not known. "No commitments recorded for this week" is an
   answer. Inventing one is not.
5. Where a belief is `low` confidence, say so in the sentence that carries
   it rather than in a footnote: "provisionally, and from one mention".
6. Point at the wiki page when the question is broad — the compiled page
   is the readable form of the same facts.
7. Write no `AttentionEvent`. Reading is not an event; a log of every
   question asked would drown the log of things actually done.

## Staging discipline

Nothing to stage. This skill has no write path at all, and that is the
point: a person must be able to ask what the App believes without the act
of asking changing anything.

If a recall surfaces something visibly wrong, say so and offer the
correction — then hand to `context_correct`, which stages the supersede.
Do not fix it in passing.

## Forbidden patterns

- Answering from the stream when a fact exists, or presenting an
  unpromoted observation as a belief.
- Reporting a fact without its confidence and last-confirmed date.
- Inferring across facts to fill a gap — "works with Sarah" plus "MMG is
  an engagement" does not license "Sarah works at MMG".
- Quoting superseded facts as current.
- Writing, updating or promoting anything.
- Softening or inflating confidence to make an answer sound better.
- Answering about the person's context in a workspace this App is not
  installed in. Facets only narrow: the org-facing and system facets never
  read this App.

## Example

> **User:** "What do you know about Sarah?"

1. `integral_list_tracks`; `integral_get_track_schema` on people.
2. `integral_query_entries` on people for "Sarah",
   `status` in (`provisional`, `confirmed`) → one Person, `confidence:
   medium`, `last_confirmed` three days ago, `standing` "carries work to
   MMG's board", four `sources`.
3. `integral_get_related` → her `arenas` link to MMG, her `register` to a
   Voice fact, and one open Commitment names her as counterparty.
4. Reply:
   > Sarah — the person who carries work to MMG's board. Medium
   > confidence, last confirmed three days ago, from four observations.
   > She is linked to the MMG engagement, and there is one open commitment
   > with her: the deck, due Friday. How you talk to her is recorded
   > separately as a Voice register — provisionally, from one example.
5. Nothing written.
