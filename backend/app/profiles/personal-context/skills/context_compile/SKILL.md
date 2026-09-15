---
name: context_compile
description: "Regenerates the Personal Context wiki from the typed facts — About me, Arenas, People, Commitments, How I decide, Voice, Rhythms, Decisions — so every paragraph cites the facts behind it and each page carries how sure and how recent it is. Preserves user-authored sections verbatim across recompiles."
spec: jv
extends: action:integral/embedded_integral_action
requires-actions:
  - EmbeddedIntegralAction
allowed-tools:
  - integral_list_tracks
  - integral_get_track_schema
  - integral_query_entries
  - integral_get_related
  - integral_create_entry
  - integral_update_entry
tags:
  - personal-context
  - wiki
---

# context_compile — SOP

The readable face of everything the App believes. Facts are rows; pages are
how a person actually reads them.

Two rules, and the second is the one that gets broken.

**Every paragraph cites its facts.** A sentence on a page that no fact
supports is the App inventing something about somebody. The citation is what
makes the difference checkable.

**User words are never overwritten.** Anything between
`<!-- user:begin -->` and `<!-- user:end -->` is the person's own writing.
It survives every recompile, verbatim, in place. A person who finds their
own paragraph gone stops writing them, and the App loses the best source it
has.

## When to use

- The nightly run, after `context_promote`.
- After a correction lands — the page carrying the superseded fact is now
  wrong on screen.
- The person asks to see or refresh their context.

## When NOT to use — delegate

- **Turning observations into facts** — `context_promote`. Compile reads
  facts; it never promotes, and a gap on a page is not a reason to invent
  one.
- **A correction** — `context_correct`. Editing a compiled sentence is a
  correction to the fact behind it; this skill regenerates from facts and
  would simply overwrite the edit.
- **Answering a question** — `context_recall`. Do not compile a page in
  order to answer something.
- **Deciding what is true** — compile has no opinion. It renders.

## Grounding (read before write)

1. `integral_list_tracks` — resolve the Personal Context tracks.
2. `integral_get_track_schema` on `pages` — `parent`, `kind`,
   `compiled_from`, `compiled_at` field keys.
3. `integral_query_entries` on every fact track, filtered to `status` in
   (`provisional`, `confirmed`). Superseded and archived facts are history;
   they belong only on the Decisions page and in a fact's own trail.
4. `integral_query_entries` on `pages` — the existing tree. Compile UPDATES
   pages; it does not delete and re-create them, or every page's id changes
   and every link breaks.
5. `integral_get_related` to walk `sources` for the citations.

## Procedure

1. Ground, per the section above.
2. Reconcile the tree against the facts:

```
About me                    ← Role, Ambition, Heuristic, Voice, Rhythm
├─ Arenas                   ← one child per Arena
├─ People                   ← one child per Person
├─ Commitments              ← open loops by horizon
├─ How I decide             ← Heuristics, each with its evidence
├─ Voice                    ← registers, with examples
├─ Rhythms
└─ Decisions                ← the ledger, by arena
```

3. For each page: read the existing body, **extract the user blocks**,
   regenerate the compiled prose, then re-insert the user blocks where they
   were. Extraction happens BEFORE generation, never after — generate first
   and the blocks are already gone.
4. Cite as you write. Every paragraph names the fact ids it rests on.
5. Set `compiled_from` to those ids and `compiled_at` to now.
6. Footer, on every page, maintained by this skill:
   `believed since <first_seen> · last confirmed <last_confirmed> ·
   N sources · confidence <low|medium|high>`
   Use the weakest confidence on the page, not the average. A page is only
   as sure as its shakiest claim.
7. A page whose facts have all been superseded is not deleted — it says so
   and links to what replaced them.
8. Write one `AttentionEvent{kind: compiled}` naming the pages touched.

## Staging discipline

Pages are what the person reads, so a rewrite is a change to them and
stages like any other fact write — under `review_daily`, batched with
`integral_begin_batch` … `integral_commit_batch` so a nightly compile is one
card, not one per page.

Under `promotion_policy: always` it lands directly and the AttentionEvent is
the record.

The `pages` track is deliberately NOT in the App's unstaged set. Observations
and the attention log land unstaged because they are notes; a page is a
statement about somebody, rendered for them to read.

## Forbidden patterns

- Overwriting a `<!-- user:begin -->` block, or moving it.
- Writing a sentence no fact supports — including "helpful" connective prose
  that reads as inference.
- Deleting and re-creating a page instead of updating it. Ids are referenced.
- Rendering superseded facts as current.
- Promoting, correcting or creating a fact to fill a hole in a page. An
  empty section is an honest page.
- Averaging confidence, or quoting the strongest fact's confidence for the
  whole page.
- Compiling when nothing changed — a nightly no-op still rewrites
  `compiled_at` and buries the real changes in the Attention Log.

## Example

> **Nightly, after promotion.** Two facts changed: one new Commitment, and
> Sarah's Person row gained a sharper `standing`.

1. Ground: tracks, `pages` schema, current facts, existing tree.
2. Two pages are affected — `Commitments` and `People/Sarah`. The other six
   are untouched and are not rewritten.
3. `People/Sarah`: read the body, lift the user block ("*Met at the Lagos
   summit — she introduced me to the MMG board.*"), regenerate the compiled
   prose from her Person fact and its four sources, re-insert the block
   under the same heading.
4. Footer: `believed since 2026-06-02 · last confirmed 2026-08-27 ·
   4 sources · confidence medium`.
5. `integral_begin_batch`, two `integral_update_entry` proposals,
   `integral_commit_batch(summary="Refreshed 2 pages")`.
6. One `AttentionEvent{kind: compiled}` naming both page ids.
