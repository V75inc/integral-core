# ADR-006 — Unstaged writes to the Personal Context stream

**Status:** Accepted 2026-08-28 — implemented. Invariant `I-PC-01` lives in `docs/INVARIANTS.md`.
**Date:** 2026-08-27
**Scope:** `backend/app/agentive/tooling/dispatch.py`, `backend/app/agentive/staging.py`, `backend/app/profiles/personal-context/`
**Supersedes / amends:** nothing. Adds one invariant, `I-PC-01`.

## Context

The Stage 1 brief describes this decision as "extend the scratch carve-out
(I-SCRATCH) to the Personal Context stream". **There is no scratch carve-out
to extend.** Read before writing this note:

- `I-SCRATCH-01..05` (`docs/INVARIANTS.md` §"Phase 4 — Agent Scratch Substrate
  Invariants") govern *where the scratch Track lives*, *how it is discriminated*,
  *what promotion preserves*, *that archival is a status not a delete*, and
  *which two policy gates promotion runs*. Not one of them mentions staging.
- Every `propose`-class tool stages. `_dispatch_propose`
  (`tooling/dispatch.py:590-665`) calls its stager, then either appends to an
  open batch or calls `create_staged_change`. There is no branch on Track kind,
  App slug, or entry type anywhere on that path.
- `tooling/manifest.py:71` pins the exception list explicitly:
  `_STAGING_EXEMPT_PROPOSE_TOOLS` holds five ephemeral tools —
  `integral_set_focus`, the three batch-control tools, `integral_propose_design`,
  `integral_ask_user` — and its comment states that a propose tool *not* on the
  list, "e.g. `integral_create_entry`", MUST declare a staging kind.

So the resident's writes into its own scratch Track stage exactly like every
other write today. This note proposes the substrate's **first** unstaged write
path, and should be read as that rather than as an extension of an existing one.

### Why it is needed

`context_attend` runs after every resident turn and on every entry save. It
writes verbatim observations about the person into the App's `stream` track.
Under today's rules each of those mints a StagedChange, which means:

- a person who has three conversations in an afternoon collects a queue of
  approval cards for *notes the App took*, none of which change anything they
  own;
- the approval surface — which exists so a person can refuse a substrate
  mutation — fills with items where refusal means only "do not remember that",
  and the signal in it drops;
- the design constraint the App is built around ("observe freely, promote
  deliberately") is unimplementable: everything is deliberate, so nothing is
  free.

The alternative already in the codebase is a session autonomy grant
(`staging.grant_autonomy`, `staging.py:1103`), which mints tokens
already-blessed and still renders a card. It is keyed on `(user_id,
session_id)` in a process-local dict. It does not fit: attention runs after a
turn closes and in nightly routines, across sessions and process restarts, and
it should not render a card at all.

## Decision

Two tracks in the Personal Context App — **`stream` and `attention`, and no
others** — accept unstaged writes from the App's own skills, under the owning
principal, in that principal's own personal workspace.

Proposed mechanism, smallest thing that works: the stager consults an
**App-scoped unstaged allowlist** resolved from the target Track, and returns
the existing `{"_no_stage": True, "data": ...}` envelope
(`dispatch.py:614-621`) after performing the write directly. This reuses the
escape hatch that already exists rather than adding a second one, and keeps
the decision inside the stager where the target Track is known.

The allowlist is `(app_slug, track_key)` pairs, declared in the manifest rather
than hardcoded in the substrate — `I-SUBSTRATE-01` forbids the substrate naming
a bundle, and a hardcoded pair would be exactly that.

### Exempt

- `stream` — Observation rows. Verbatim, provenance-carrying, `handled:
  pending`. Working memory *about the person*.
- `attention` — AttentionEvent rows. The transparency log. Staging the log of
  what the App did would be circular: the record of an action cannot be
  contingent on approving the record.

### Not exempt — no exceptions

- **Every fact track**: `identity`, `arenas`, `people`, `commitments`. Beliefs
  about a person are staged, batched by `context_promote` into one card a day
  under `review_daily`, or landed under an explicit `always` setting the person
  chose.
- **`pages`** — the compiled wiki. A person reads these; a rewrite is a change
  to what they read.
- **Anything outside this App.** The exemption is bounded by the two track keys
  above, in this App, in the caller's own personal workspace. It never reaches
  another workspace, another App, or another user.
- **Writes by anything other than this App's skills.** The exemption is a
  property of the target, not a capability the caller carries around.

### Consequences

- The approval surface keeps its meaning: a card is a substrate change the
  person may refuse.
- Observations are visible immediately in the Stream feed and Attention Log —
  unstaged is not invisible. Everything written is browsable, and
  `attention_enabled: false` stops it at the source.
- Silence becomes consent for *what the App noticed*, never for what it
  believes or does. That line is the one this App is built on.
- The substrate grows its first unstaged write path. That is a real widening of
  the trust boundary and the reason this is a gate.

## Invariant text (landed as `I-PC-01` in `docs/INVARIANTS.md`)

> ### I-PC-01 — Unstaged writes are bounded to the observation stream and the attention log
>
> **Scope:** `backend/app/agentive/tooling/dispatch.py`,
> `backend/app/agentive/tooling/stagers_*.py`, the Personal Context bundle manifest.
>
> **Rule:** An agent write MAY bypass staging only when ALL of the following
> hold: the target Track belongs to the Personal Context App; its manifest track
> key is `stream` or `attention`; the App is installed in the acting principal's
> OWN personal workspace; and the acting principal is that workspace's owner.
> Every other write — including every fact track in the same App, and the
> compiled `pages` track — stages. The exempt `(app_slug, track_key)` pairs are
> declared in the bundle manifest; the substrate MUST NOT name them
> (I-SUBSTRATE-01).
>
> **Rationale:** The approval surface exists so a person can refuse a change to
> their substrate. Filling it with the App's own note-taking empties it of
> meaning. Observations are not changes to what the person owns; beliefs are.
>
> **Verification:** `backend/tests/test_personal_context_unstaged.py` — a write
> to `stream` mints no StagedChange; a write to `identity` mints one; a write to
> `stream` in a workspace the caller does not own mints one (or is refused);
> the exempt set is read from the manifest and `.ci/substrate_domain_drift_check.sh`
> stays clean.

## Tests this needs

1. `context_attend` writing an Observation mints **no** token — assert the
   staged-token store is empty after the call.
2. The same skill writing a Role mints **one** token.
3. A write to `stream` in a workspace whose owner is somebody else stages (or
   is refused outright) — the exemption is bounded by ownership, not by track
   name.
4. An AttentionEvent write mints no token.
5. A `pages` write mints one.
6. `attention_enabled: false` produces no write at all, staged or otherwise.
7. The exempt set is resolved from the manifest, not from a substrate constant
   — a grep gate, in the shape of the existing single-literal gates.

## The open question, as resolved

Ruled: the narrow version, as proposed. Recorded here because the reasoning
binds the next bundle that wants this.

The proposal keys the exemption on `(app_slug, track_key)` resolved from the
manifest. The alternative is a **generic capability** — a manifest flag such as
`unstaged: true` on a track declaration, which any trusted first-party bundle
could set. That is more expressive and correspondingly more dangerous: it
generalises "does not need approval" into a property a bundle can claim for
itself. The narrow version is proposed on the grounds that one App needs it
today and the general case has no second caller yet.

What landed keeps that narrowness in the runtime rather than in the
declaration. `app.unstaged_tracks` is a per-bundle list, so the shape looks
generic — but a bundle claiming it buys nothing outside the claiming user's own
personal workspace, acting as themselves, on a track the bundle itself
materialized. A second bundle wanting the exemption gets the same bounded
thing, which is why the declaration did not need to be narrower than the gate.
