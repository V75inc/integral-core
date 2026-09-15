# ADR-007 — A decision ledger from staging's terminal states

**Status:** Accepted 2026-08-28 — implemented. Ruled: option 1, the `decisions` track.
**Date:** 2026-08-28
**Scope:** `backend/app/agentive/staging.py`, `backend/app/agentive/staging_store.py`, the Personal Context bundle
**Related:** ADR-006 / I-PC-01 (unstaged writes)

## Context

Every approval card a person acts on is a decision about their own work:
what they let the agent do, what they refused, and what they left to expire.
That is the highest-signal record of how somebody decides that this system
produces — and today it is thrown away.

What the code actually does, read before writing this:

- Three call sites move a token to a terminal state: `revoke_token`
  (`staging.py:975`), `consume_token` (`:1022`), and lazy expiry in
  `consume_token` / `_sweep_expired_locked` (`:197`, `:1011`).
- `consumed` and `revoked` converge on `_push_state_and_record_closure`
  (`:862`), which pushes a WS event, records a closure marker in the
  conversation, rewrites the FE transcript snapshot — and calls
  `staging_store.remove(sc.token)`.
- `staging_store` persists **only** `pending` and `blessed` (`_DURABLE_STATES`,
  `staging_store.py:52`). Its module docstring is explicit that terminal rows
  are dropped on purpose: reload-correctness is handled by the transcript
  rewrite, so keeping them "would only grow the table for no gain".

That reasoning is correct for its purpose. `staging_store` is a durability
mirror for *outstanding* work, and a ledger is not what it is for. The
argument here is not that the store should keep terminal rows; it is that
something else should record the decision before the row goes.

**Expiry never reaches that path at all.** `consume_token` raises
`StagingError("expired")` before any push, and `_sweep_expired_locked` marks
state without pushing (the code says a discrete expiry push would need a
background sweeper). So an expired card leaves no trace anywhere today —
including in the transcript rewrite. Any ledger that wants `expired` needs a
seam the current terminal path does not have.

### One outcome in the brief does not exist

Stage 1 specifies `outcome ∈ {blessed, edited_then_blessed, rejected,
expired}`. There is **no edit-before-bless path in the codebase**:
`bless_token` takes `(user_id, token, autonomy)` and flips state; nothing
accepts a modified payload, and `grep` for an edit/patch seam on staging
returns nothing. `edited_then_blessed` is therefore unreachable — it
describes a capability that has not been built.

Two honest options: drop the value from the enum until the capability
exists, or keep it declared and never written. **Recommended: keep it**, and
document it as reserved. The `Decision` entry type already ships with it
(phase 1), a select field carrying an unused member costs nothing, and
removing it later is a manifest edit while adding it back is a migration.
Whoever builds card editing gets a slot waiting for them, and this note is
the record of why it was empty.

## Decision (proposed)

On every terminal transition, emit a `Decision` into the Personal Context
`commitments` track: `proposal_kind` (the staged `kind`), `outcome`,
`diff` (`diff_human` — what the person was actually shown), `token_ref`,
`decided_at`, and `arena` where resolvable.

### Two ways to wire it, and the recommendation

**A. Emit a `ChangeEvent`; the App consumes it.** Add a
`staging.resolved` action to `ChangeEventAction`, emit from the terminal
path, and let the bundle write the entry.

*For:* substrate stays generic; the App owns its own writes.
*Against:* `ChangeEventAction` and `PolicyAction` are single-literal
invariants with a strict-superset relation (I-CHA / I-APPROVAL-03) — adding
a member touches both and their grep gates. And there is no
ChangeEvent→bundle-hook consumer today: `I-HOOK-01` freezes the catalog at
seven entry/connector points, none of which is a staging event. This route
needs a new hook point, which is a frozen-catalog amendment.

**B. Call a bundle-facing writer from the terminal path.** In
`_push_state_and_record_closure`, before `staging_store.remove`, invoke a
resolver that writes the `Decision` when the acting user has the App
installed, and no-ops otherwise.

*For:* no new hook point, no literal change, no frozen-catalog amendment.
Sits exactly where the data still exists.
*Against:* substrate code calling into an App-shaped concern — mitigated the
same way ADR-006 was: the substrate names no bundle, resolving the target
through the manifest (`resolve_personal_context`, which already exists and
already returns the track map).

**Recommended: B**, extended to cover expiry — which means giving the lazy
expiry paths a call site they currently lack. That is the only new seam in
this proposal, and it is the one that makes "what I let expire" recordable
at all.

### Where it must not go

- **Not on the person's timeline as a belief.** A `Decision` is a record of
  an event that happened, not a claim about the person that could later turn
  out to be wrong. It carries no belief fields and is never superseded — that
  is why phase 1 gave `Decision` no `confidence` / `status` /
  `superseded_by`, unlike every other type in the App.
- **Not staged.** Recording that a person approved something cannot itself
  require approval. Under ADR-006's bounds that means `commitments` would
  have to be exempt — and it must not be, because it holds `Commitment`
  rows, which are beliefs. **Open question below.**
- **Never blocking.** A ledger write must never fail a bless. Best-effort
  and logged, like every other call on that path.

## The open question, as resolved

**Ruled: option 1.** `Decision` moved to its own `decisions` track, declared
unstaged alongside `stream` and `attention`. I-PC-01 stays a whole-track rule,
and the ledger is separated from beliefs in the model rather than only in the
prose.

What that changed, beyond the manifest: `commitments` now holds `Commitment`
alone and keeps staging; the decisions table and feed moved with the type;
`Page.compiled_from` reaches the new track; and the unstaged declaration is
three keys against a cap of four.

The original text follows.

The `Decision` type lives in the `commitments` track, which is NOT unstaged
and must not become unstaged — it holds `Commitment` beliefs. So a ledger
write either mints an approval card for the act of recording an approval
(absurd), or needs a narrower exemption than ADR-006 currently expresses.

Three ways out, in the order I would rank them:

1. **Move `Decision` to its own track** (`decisions`), and declare that
   track unstaged alongside `stream` and `attention`. Cleanest: the
   exemption stays a whole-track property, which is what I-PC-01 says, and
   the ledger is separated from beliefs in the model as well as in the
   prose. Costs a manifest change and a wiki page target.
2. **Extend the exemption to `(track, entry_type)` pairs.** More precise,
   and a real widening of I-PC-01's shape — a rule about tracks becomes a
   rule about rows, which is harder to reason about at a glance.
3. **Write the ledger from the substrate directly**, bypassing the agent
   dispatch seam entirely, on the grounds that it is not an agent write at
   all — the person is the actor. Arguably the most honest framing, and the
   biggest departure, since it puts a second write path next to the one
   ADR-006 just went to some trouble to keep singular.

**Recommendation: option 1.** It fits the invariant as written, it needs no
new concept, and "the ledger is not a belief" is a distinction worth having
in the model rather than only in a comment.

## Tests this needs

1. Bless → one `Decision` with `outcome=blessed`, `token_ref`, `diff`.
2. Reject → `outcome=rejected`.
3. Expire → `outcome=expired` (the path that does not exist yet).
4. A user without the App installed → no write, no error.
5. A ledger-write failure does not fail the bless — the token still resolves.
6. The `Decision` carries no belief fields and is never superseded.
7. The substrate names no bundle on this path (grep gate, as ADR-006).

## What shipped, and what changed on the way

Two things the note anticipated turned out slightly differently in the code:

**Expiry needed less new machinery than expected.** The sweeper
(`_sweep_expired_locked`) is sync and holds the lock, so it queues newly-expired
tokens and async callers drain the queue once released — `bless`, `revoke`, the
pending polls, and a `finally` on `consume_token` that runs on the expiry raise
itself. Every one of those drains is load-bearing; removing the `finally` fails
a named test.

**A second queueing site turned out to be dead code.** The lazy-expiry branch
inside `consume_token` also queued, which looked like sensible
belt-and-braces. It is unreachable: the sweeper at the top of the same call has
already flipped and queued every in-memory token that aged out, and a token that
expired while the process was down never gets that far because
`staging_store.load` deletes expired rows and reports them absent — hydration
fails first with `unknown_token`. Mutation-testing found it (disabling the branch
broke nothing), and it was removed rather than left to suggest two paths where
there is one.

## What I would not do without a further ruling

Record the *payload* of what was approved. `diff_human` is what the person
saw and is the honest record of what they agreed to. The machine payload can
carry entry bodies, field values and node ids — copying it into a
long-lived, human-browsable ledger is a data-retention decision, not an
implementation detail.
