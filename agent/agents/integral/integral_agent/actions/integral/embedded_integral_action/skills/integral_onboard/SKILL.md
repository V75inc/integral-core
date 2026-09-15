---


name: integral_onboard
description: "Guides a new user or workspace through first setup across multiple turns — asks clarifying questions, provisions apps/tracks, and delegates schema work to integral_scaffold or integral_model as needed."
spec: jv
allowed-tools:
  - integral_whoami
  - integral_list_apps
  - integral_list_tracks
  - integral_list_profiles
  - integral_describe_substrate
  - integral_propose_design
  - integral_begin_batch
  - integral_create_app
  - integral_create_app_track
  - integral_apply_profile_to_track
  - integral_author_profile
  - integral_save_view
  - integral_create_entry
  - integral_author_skill
  - integral_commit_batch
  - integral_cancel_batch
requires-actions:
  - EmbeddedIntegralAction
extends: action:integral/embedded_integral_action
tags:
  - integral
  - onboard
  - setup
  - multi-turn
  - apps

---

# Integral onboard — SOP

## Purpose / when to use

A user is **new**, or has an **empty/near-empty workspace**, and needs to be
walked from "I have nothing" to "I have a working first area". Their words sound
like:

- "I'm new — help me get started."
- "What should I do first?"
- "Set up my workspace for me."
- "I just made an organization workspace; help me populate it."

The deliverable is a **guided multi-turn flow**: a couple of clarifying questions
to learn what the user actually wants to track, then a concrete first structure
stood up for their approval. Onboarding is the only base skill that is explicitly
**multi-turn** — it asks, listens, then acts.

This skill is the **conversation coordinator**. It does not own the structure
build itself — once intent is clear, it runs the **scaffold flow** (the same
batch-of-creates pattern as `integral_scaffold`) to stand up the first area.

## When NOT to use this → delegate

- **The user already stated a clear, specific intent** ("set up a CRM") with no
  need to gather context → go straight to **`integral_scaffold`**; skip the
  question phase.
- **The user already has apps/tracks** and wants to add or change structure →
  **`integral_model`** (deepen schema) or **`integral_workspace`** (add a track).
- **Deep schema / relation design** → **`integral_model`**.
- **Filing content / entry CRUD / bulk reorg** → **`integral_filing`** /
  **`integral_entries`** / **`integral_organize`** respectively.

Onboard is for the *cold-start, "I don't know where to begin"* case. The moment
intent is concrete, it converges to the scaffold flow.

## Grounding — read before you ask, read before you build

1. **`integral_whoami`** / scope — who the acting user is and which workspace
   (personal vs organization) they are in. Onboarding lands in the **active**
   workspace.
2. **`integral_list_apps`** / **`integral_list_tracks`** — is the workspace
   actually empty? If it already has structure, **do not re-onboard** — orient the
   user to what exists and hand to `integral_model`/`integral_workspace`. Onboard
   is for genuinely empty/near-empty starts.
3. Before any build step: **`integral_list_profiles`** (prefer an existing library
   package) and **`integral_describe_substrate`** (only propose field/view types
   the substrate supports) — exactly as `integral_scaffold` grounds.

## Procedure — ask first, then run the scaffold flow

This is a **two-phase, multi-turn** procedure. Do not stage any structure until
phase 1 has produced concrete intent.

**Phase 1 — clarify (no writes).** Ask a *small* number (2–3 max) of focused
questions, then stop and let the user answer:

1. "What do you most want to keep track of in here?" (the domain — projects,
   clients, content, tasks, …).
2. "Is this just for you, or will others collaborate?" (informs visibility, but
   default `private`; only widen on explicit ask).
3. Optionally: "Roughly what does one item look like?" (hints at entry types /
   fields).

Do **not** front-load a long questionnaire. Ask, wait, refine. If the user gives
enough in one breath, skip straight to phase 2.

**Phase 2 — stand up the first area (one bless).** Once intent is concrete, run
the **scaffold flow** — the identical batched pattern as `integral_scaffold`:

Before opening the batch: state the planned shape in plain language and call
`integral_propose_design` (one-line `summary`), then let the user confirm —
the backend refuses a new-app build otherwise. Onboarding's clarify phase
already gathers intent; this records the proposal so the build gate passes.
Then run the scaffold flow:

1. **`integral_begin_batch`** with a label (e.g. "Get started").
2. **`integral_create_app`** for the domain (skip if extending an existing app).
3. **`integral_create_app_track`** — the obvious starter track(s) for the intent.
4. **Shape the tracks** — `integral_apply_profile_to_track` if a library package
   fits (preferred), else `integral_author_profile` from the gathered brief. For
   anything richer than a starter shape, note it and offer to continue in
   `integral_model`.
5. **`integral_save_view`** — a sensible default view per track.
6. **`integral_create_entry`** — 1–3 illustrative seed entries so the structure
   isn't empty (skip if the user wants it bare).
7. **Repeatable procedure → app-scoped skill.** If onboarding surfaced a recurring
   on-demand procedure the user wants to invoke by phrase ("every time…", "let me
   just say 'X'"), author it INSIDE this batch with
   `integral_author_skill(app_id="{{app.id}}", …)` — an app-scoped skill, not a
   scheduled task (`queue_task` is disabled; clock work uses skill
   `integral_scheduling`) and not schema. See `integral_scaffold`'s
   procedure step 7 for the full contract.
8. **`integral_commit_batch`** with a summary → one combined approval card. **Wait
   for the bless.**

After the bless, point the user at what they can do next ("add real items by just
telling me about them; ask me to refine the structure any time").

## Staging discipline

- **Phase 1 asks no approval and writes nothing** — questions are plain
  conversation. Never stage structure before you understand the intent.
- **Phase 2 is batched**: every create/apply/save/seed accumulates into one card
  via `begin_batch`/`commit_batch`; the user blesses the whole first-area plan once.
- Present the combined card plainly and **wait**. Never say "set up" / "created"
  until `[SYSTEM:STAGING-RESOLVED] … state=consumed`. A `revoked` marker means the
  user wants something different — ask what to change rather than rebuilding blind.
- If the user reconsiders mid-build, **`integral_cancel_batch`**; nothing is
  written.

## Forbidden patterns

- Staging any structure **before** asking what the user wants to track (skipping
  phase 1 when intent is genuinely unclear).
- **Over-interrogating** — more than ~3 clarifying questions before acting; ask,
  then build.
- **Re-onboarding a non-empty workspace** — check `integral_list_apps`/`list_tracks`
  first; orient instead of duplicating.
- Emitting **one approval card per create** instead of batching the first-area
  build into a single bless.
- Authoring a profile from scratch when a **library package** fits
  (`integral_list_profiles`).
- Proposing field/view types not in `integral_describe_substrate`.
- Designing deep multi-type/relational schema inline — hand that to
  `integral_model` after the basics land.
- Claiming the workspace is "set up" before the staging-resolved marker fires.

## Example walkthrough

> **User:** "Hi, I'm new here — help me get going."

**Turn 1 (clarify):**
1. `integral_whoami` → personal workspace, no apps.
2. `integral_list_apps` → empty (confirmed cold start).
3. Reply (no writes): "Happy to. Two quick things: what do you most want to keep
   track of in here, and is it just for you or shared with others?" **Wait.**

> **User:** "Freelance projects and the clients behind them. Just me for now."

**Turn 2 (propose, then STOP):**
4. `integral_list_profiles(type_hint="projects")` → a "Projects" package fits.
5. `integral_describe_substrate` → confirm `table`/`kanban` views.
6. Propose in one line: "I'll stand up a **Freelance** app with **Projects**
   and **Clients** tracks, applying the Projects package, with a board view."
   then call `integral_propose_design(summary="Freelance app: Projects +
   Clients, Projects package, board view")` — **once**.
7. **End the turn — wait for the user to confirm.** (`integral_commit_batch`
   rejects the build otherwise — `design_not_proposed`.)

> **User:** "Yep, go for it."

**Turn 3 (build — do NOT call `integral_propose_design` again):**
8. `integral_begin_batch(label="Get started")`.
9. `integral_create_app(name="Freelance", description="Projects and clients.")`.
10. `integral_create_app_track(app_id=<freelance>, name="Projects")`.
11. `integral_create_app_track(app_id=<freelance>, name="Clients")`.
12. `integral_apply_profile_to_track(track_id=<projects>, profile_template_id=<pkg>)`.
13. `integral_save_view(track_id=<projects>, name="Board", view_type="kanban",
    config={group_by:"status"})`.
14. `integral_create_entry(track_id=<projects>, title="Example Project", text="…")`.
15. `integral_commit_batch(summary="Freelance app: Projects + Clients, board view,
    1 sample project.")` — the propose_design in turn 2 lets this pass the gate.
16. Reply: "Staged a **Freelance** app with Projects + Clients, a board view, and a
    sample project — approve to set it up. Once it's live you can add real items
    just by telling me about them, and I can wire Projects to reference Clients
    whenever you're ready." **Wait for bless.**

When the user later wants Projects to point at Clients, hand the relation design to
`integral_model`.
