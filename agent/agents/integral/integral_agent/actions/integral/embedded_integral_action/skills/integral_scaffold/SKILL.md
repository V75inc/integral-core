---


name: integral_scaffold
description: "Stands up a new app or domain from user intent in two beats — (1) propose the design and wait for a chat confirm/correction, (2) batch the build and wait for Prompt Sheet Approve. Use for greenfield workspace setup; delegates ongoing schema tuning to integral_model."
spec: jv
# Prefer-heavy is documented intent for harnesses that honor it. Integral's
# agent.yaml sets planning_heavy_first_tick: true so tick 0 is already heavy —
# light gear must not own greenfield scaffold (it skips use_skill and replies).
# First tool this skill expects after activation: continue the propose/build SOP
# (never a free-form "approve in Integral" prose bypass).
allowed-tools:
  - integral_whoami
  - integral_describe_substrate
  - integral_list_profiles
  - integral_list_apps
  - integral_ask_user
  - integral_propose_design
  - integral_begin_batch
  - integral_create_app
  - integral_create_app_track
  - integral_apply_profile_to_track
  - integral_author_profile
  - integral_modify_profile
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
  - scaffold
  - setup
  - apps
  - batch

---

# Integral scaffold — SOP

## Purpose / when to use

The user wants a **whole working area** stood up from a sentence of intent, not
one track at a time. Their words sound like:

- "Set me up a CRM."
- "Build a place to track my freelance projects."
- "I need a lightweight bug tracker / content calendar / reading list."
- "Scaffold an app for our hiring pipeline."

The deliverable is a coherent starting structure — an **App**, one or more
**Tracks**, a **Content Profile** giving those tracks shape, at least one saved
**View**, and a handful of **seed entries** that demonstrate the structure.

There are **two separate beats** (do not collapse them):

1. **Design confirm (chat)** — `integral_propose_design` renders the design
   card. The user replies in chat to confirm or correct. There is **no**
   Prompt Sheet Approve on this beat.
2. **Build bless (Prompt Sheet)** — after they confirm, you `begin_batch` →
   create/apply/save → `commit_batch`. **That** opens the one Prompt Sheet
   Approve card. Nothing exists until they Approve it.

This skill is the **coordinator**: it sequences the calls and batches them. It
does **not** itself design entry types, fields, or relations in depth — that is
`integral_model`'s job. Scaffold sets up the skeleton; model puts meat on it.

## When NOT to use this → delegate

- **Deep schema design** — choosing entry types, fields, lookup-vs-anchor
  relations, mixed-type tracks → **`integral_model`**. Scaffold calls model's
  primitives for a *starter* shape; anything beyond a sensible default belongs in
  a modeling conversation.
- **Filing freeform content** into an existing structure → **`integral_filing`**.
- **Plain entry CRUD** ("add a task", "edit this record") → **`integral_entries`**.
- **A single new track in an existing app** with no app/profile/view setup →
  **`integral_workspace`** (just `integral_create_app_track`).
- **Bulk reorganizing / migrating existing entries** → **`integral_organize`**.
- **A guided, multi-turn new-user/workspace walkthrough** → **`integral_onboard`**
  (which calls *this* skill once it has gathered enough intent).

If the user already has the app and just wants more inside it, you are probably
in `integral_model` or `integral_workspace` territory, not here.

## Grounding — read before you build

Never scaffold blind. Before opening the batch:

1. **`integral_whoami`** / scope — confirm the acting user and that you are in the
   workspace they mean. Scaffolding lands in the **active** workspace; you cannot
   widen scope with a tool arg.
2. **`integral_list_apps`** — does an app for this intent already exist? If a
   matching app is present, do **not** create a duplicate — switch to adding
   tracks/profile to it (and consider handing to `integral_model`).
3. **`integral_list_profiles`** — is there a **library package** that already fits
   the intent (a "CRM", "Bug Tracker", "Tasks" package)? **Always prefer applying
   an existing package** (`integral_apply_profile_to_track`) over authoring a
   profile from scratch.
4. **`integral_describe_substrate`** — the global palette of field types and
   view-palette keys. Only ever propose view types and field types this confirms
   the substrate can render.

If grounding shows the intent is already half-built, say so and propose the
*delta*, not a fresh duplicate.

## Propose before you build

The backend ENFORCES this: `integral_commit_batch` REFUSES to stage the
build of a new app until you have called `integral_propose_design` AND the
user has responded in a later turn. Skipping it just wastes a turn on a
recoverable error. So:

1. **Reuse first.** Consult `integral_list_profiles`. If a library package
   fully fits the domain, plan to apply it (`integral_apply_profile_to_track`);
   if one partly fits, apply-and-extend; only if none fits, a fresh shape.
   Say which in your proposal — the user should know when they're getting
   proven structure rather than a hand-rolled one.
2. **Resolve the fork first — only if there is one.** Sometimes the request
   has a branch you cannot settle for the user: build standalone vs. extend
   an app they already have, one track vs. several, private vs. shared. When
   that branch is a choice between 2–6 concrete paths, call `integral_ask_user`
   with the question and those options, and **end your turn there** — they
   answer with one click and you propose on the next turn, already knowing
   the shape.

   Do NOT ask and propose in the same breath; a fork tacked onto the end of a
   proposal is the thing this step exists to replace. Skip this step entirely
   when a reasonable inference covers it, when the user already named concrete
   tracks/fields, or when the question is open-ended enough that prose serves
   better. Never ask what `integral_list_apps` / `integral_list_profiles` /
   a `describe` tool would answer — check first, then ask only what is left.
3. **Propose via the card — never as freeform chat.** Put the planned Tracks,
   their key fields, any Views, and whether you're applying/extending a package
   or building fresh **only** in the `proposal` argument of
   `integral_propose_design` (multi-paragraph markdown is fine). Call that tool
   in **this same turn** as soon as you know the shape — do not first answer
   with a prose "here's a proposed structure" and only card it later. The chat
   UI renders `proposal` as the design card; that card is the **only** design
   surface. Putting the expansion only in reasoning (collapsed) is a defect.
   **Do not** paste tracks/fields/views into assistant chat text. At most one
   short closer after the tool ("Confirm or correct the design card."). If the
   user already named concrete tracks/fields, still put that restated shape
   into `proposal` (terse is OK as long as fields are listed).
4. **Record + wait (this ends your turn).** Call `integral_propose_design`
   once with:
   - `summary` — one-line audit label
   - `proposal` — the full plain-language design from step 3 (≥ ~120 chars)
   Then STOP. Further tools are refused until the user replies; do not open
   the batch in the same turn.
5. **On the NEXT turn, after the user responds:**
   - **Correction** (add/remove tracks, change fields) → call
     `integral_propose_design` again with the updated `summary`/`proposal`,
     then STOP and wait again. Re-propose is allowed while the design is
     still unapproved; it errors with `already_proposed` only after the
     design was approved / the build gate already passed.
   - **Affirm** ("yes", "looks good", "build it", "go ahead", "do it") →
     this is a **build** turn, not a design turn. Call **only**:
     `integral_begin_batch` → create/apply tools for the blessed shape →
     `integral_commit_batch`, then STOP.
     **Do not** call `integral_propose_design` on an affirm turn — even if
     you amended earlier. Re-propose only when the user asks for further
     shape changes (tracks/fields/views). Do **not** re-ground
     (whoami/list_*/describe_substrate), and do **not** thrash `update_plan`.
     The Prompt Sheet opens for the **build** card only — wait for that
     Approve. Do **not** claim the app exists yet.
   Only if you never proposed at all and commit returns `design_not_proposed`
   should you propose, then wait — never retry the build blindly.

## Procedure — one batch, one bless

**Describe everything you create.** Every app, track, and entry type you stage
MUST carry a concise, specific one-line `description` — what it is *for*, in the
user's domain terms (e.g. a Contacts track → "People and organizations you do
business with."). Never leave a description blank or generic ("A track.") when
the tool accepts one — a blank description is a defect the user sees on every
card and settings page. Derive each blurb from the user's stated intent; do not
invent scope they did not ask for.

Scaffolding is a multi-step workflow, so it **must** stage as a single approval.
Wrap the whole sequence in a batch:

1. **`integral_begin_batch`** with a short `label` (e.g. "Set up CRM"). This is the
   **VERY FIRST** tool call of the build — call it **before** `integral_create_app`
   and before any other create/apply/save. Everything proposed after it collects
   into one card.
2. **`integral_create_app`** — the app (schema/database) for the domain, as the
   **first operation INSIDE the batch** (right after `begin_batch`). Give it a clear
   `name` and a specific one-line `description` (never blank). The tracks in steps 3–6 reference it as
   `{{app.id}}`, which only resolves when `create_app` is in **this same batch** — so
   never stage the app as a separate card from its tracks. Skip `create_app` only
   when extending an existing app (then resolve that app's real id per the
   anti-spray rule and use it directly, no `{{app.id}}`).
3. **`integral_create_app_track`** (preferred) — one call per starter track the
   intent implies. Keep the starter set small and obvious (e.g. CRM → "Contacts",
   "Deals"). Give every track a concise one-line `description` (its purpose) —
   pass it on the create call; do not leave it blank. Default `visibility` is
   `private`; only set `org`/`public` when the user explicitly asks.
   - **The app does not exist yet at stage time** (it is step 2 of THIS batch), so
     you cannot know its id. Set `app_id` to the literal token **`{{app.id}}`** (never the app display name) —
     the batch resolves it to the real id of the app created in step 2 at approval
     time. Use this exact token; do not invent an id and do not omit `app_id`.
   - Every track in this batch belongs to the **one** app from step 2. Never create
     the same track in more than one app.
   - **Referencing in-batch ids.** Tracks, views, and seeds you create in this
     batch also do not have ids until approval. Reference the app as `{{app.id}}`.
     For a track, **prefer the named token `{{track.id:<Track name>}}`** (e.g.
     `{{track.id:Authors}}`) — it resolves to the track created with that exact
     title regardless of order, so a view or entry always lands on the track you
     mean. The bare `{{track.id}}` resolves only to the *last* track created, so
     it silently mis-targets when several tracks exist before you populate them —
     use the named form whenever the batch creates more than one track. Likewise
     reference a seeded entry (e.g. a relation value pointing at another record)
     as `{{entry.id:<Entry title>}}`. Names must match the title you gave the
     object exactly.
4. **Give the tracks shape** — pick exactly one path per track:
   - **Library package fits** → `integral_apply_profile_to_track(track_id,
     profile_template_id)` (from `integral_list_profiles`). Preferred when a
     package matches the intent.
   - **No package fits (custom track)** → pass the track's `entry_types`
     **inline to** `integral_create_app_track`. This materializes the fields
     ONTO the track in one call, so its "+New" form shows them. Do **NOT**
     author a separate profile and hope it attaches — a standalone
     `integral_author_profile` is a *library package*, it does NOT shape the
     track you just created, so the track would keep the empty generic "Post"
     type (the "+New Post shows no fields" bug).
     ```
     integral_create_app_track(
       app_id="{{app.id}}",
       name="Training Records",
       description="Employee training completions and their expiry.",
       entry_types=[
         {"name": "Training Record", "icon": "document",
          "description": "One training a person completed, with status and dates.",
          "fields": [
           {"key": "employee_name", "name": "Employee name", "type": "text"},
           {"key": "training_type", "name": "Training type", "type": "select",
            "enum": ["onboarding", "compliance", "technical", "leadership"]},
           {"key": "status", "name": "Completion status", "type": "select",
            "enum": ["not_started", "in_progress", "completed", "expired"]},
           {"key": "completion_date", "name": "Completion date", "type": "date"},
           {"key": "expiration_date", "name": "Expiration date", "type": "date"}]}])
     ```
     Each entry type takes an optional one-line `description`; each field is
     `{key, name, type, enum?}` — the same shape
     `integral_author_profile` takes. Use `integral_author_profile` only when the
     user wants a **reusable library package** (not a one-off track).
   - **A simple lookup relation between two of this app's tracks** (e.g. an
     Affiliate Link that points at a Campaign) is fine to declare inline — but a
     relation whose target lives in a **different track is cross-track**, so its
     `relation` object MUST set `allow_cross_track: true` and name the target
     track in `target_track_types` (plus the allowed type in `target_entry_types`).
     A bare `target_entry_types` alone is rejected at create. The `relation`
     config is **nested under the field**, never flat on the field spec:
     ```
     {"key": "campaign", "name": "Campaign", "type": "relation",
      "relation": {"target": "entry", "target_track_types": ["campaigns"],
                   "target_entry_types": ["campaign"], "allow_cross_track": true,
                   "many": false}}
     ```
     For anything richer — expansion/anchor relations (`target: "track"`),
     mixed-type tracks, or a multi-relation graph — stop and hand to
     `integral_model`.
5. **`integral_save_view`** — **REQUIRED: at least one default view per track**
   (usually `table`; use `kanban` when there is a status/stage field). Without
   a view the track opens blank even when fields exist. Use only `view_type`s
   `integral_describe_substrate` confirms. Reference tracks as
   `{{track.id:<Track name>}}`.
6. **`integral_create_entry`** — **REQUIRED demo seed data** unless the user
   explicitly asked for an empty/bare structure. Seed **2–4 illustrative
   entries per track** in the same batch so Approve produces a demo-ready app
   (fields, relations, and views can be validated immediately). Rules:
   - Label seeds clearly as demo ("Demo Car — Blue Sedan", "Sample Rental #1").
   - Do **not** fabricate realistic PII (no real emails/phones/SSNs).
   - Wire **relations between seeds** with `{{entry.id:<Entry title>}}` so
     cross-track links are real (e.g. a Rental pointing at a Demo Car and a
     Demo Renter).
   - Seed **after** views are staged; still inside the same batch before
     `commit_batch`.
7. **Bundle a repeatable procedure as an app-scoped skill** — *only* when the
   user describes a recurring, on-demand procedure they want to invoke by phrase
   ("every time X, do Y", "let me just say 'screen this candidate'", "I always
   handle these the same way"). That is a **skill**, not schema and not a
   scheduled job. Author it INSIDE this batch with
   `integral_author_skill(app_id="{{app.id}}", name=…, description=…,
   body_override=<SOP markdown>)` — it stages like every other op, so it lands on
   the same approval card as the app. Keep it app-scoped: pass `app_id` and let
   `private` default to true, so the skill only surfaces when the resident is
   working in this app.
   - Write `body_override` as a short SOP: a when-to-use line, the numbered
     steps, and the `integral_*` tools each step calls (the same shape as this
     skill). `tools_required` is optional — omit if unsure.
   - **This is NOT a scheduled/automated task.** Do not reach for `queue_task`
     (disabled here) or any timer/trigger for an on-demand "when I say X"
     procedure — that is a skill. Clock-based work belongs to skill
     `integral_scheduling`, not scaffold.
   - **Honesty:** never tell the user you have created or registered a procedure
     unless the `integral_author_skill` call is staged in this batch — and even
     then it is only real once the card is approved. A tool that errored is not
     done; say so plainly rather than claiming success.
8. **`integral_commit_batch`** with a one-line `summary` — presents the **single**
   combined approval card. Stop and wait for the bless.

If the user changes their mind mid-build, **`integral_cancel_batch`** — nothing is
written.

## Staging discipline

- **Design beat ≠ build beat.** After `integral_propose_design`, STOP. Do not
  open a Prompt Sheet for the design — the design card + chat reply are the
  confirmation. The Prompt Sheet appears only after `integral_commit_batch`.
- Every create/apply/save/seed call between `begin_batch` and `commit_batch` is a
  **propose** — it accumulates into the batch, it does **not** apply. The user
  blesses the whole plan once via the Prompt Sheet after commit.
- After `commit_batch`, present plainly: "I've staged an app *‹name›* with
  tracks *‹a, b›* — approve the Prompt Sheet card to build it." Then **wait**.
- **Never** say "created", "set up", "being set up", or "built" until
  `[SYSTEM:STAGING-RESOLVED] … state=consumed` for the **batch** token. Until
  then it is "staged for your review". A `revoked` marker means the user
  declined — do not silently rebuild.
- If any propose call returns an error envelope, surface it verbatim and stop;
  do not commit a half-formed batch or retry blindly. If `commit_batch`
  returns `batch_empty` / an error, say so — do not invent success.
- Call `begin_batch` **once** per build. A second `begin_batch` must not wipe
  work already staged into the open batch (the backend keeps ops).

## Forbidden patterns

- **Building a new app without first calling `integral_propose_design` and
  letting the user respond.** The backend enforces this
  (`integral_commit_batch` refuses a new-app batch otherwise), so skipping it
  just wastes a turn on a recoverable `design_not_proposed` error. Propose the
  shape, record it with `integral_propose_design`, wait for the user, then build.
- **Two design beats.** Never write a plain-language track/field/view design in
  the assistant message and then call `integral_propose_design` on a later turn
  (or after the user says "yes"). That forces them through freeform confirm
  *and* the design card. One surface only: the proposal card in the first
  greenfield turn.
- **Empty shell apps.** Never `commit_batch` a greenfield that only has
  `integral_create_app` and/or `integral_author_profile`. Every proposed track
  must be an `integral_create_app_track` in the same batch with `entry_types`
  inline (or `integral_apply_profile_to_track`). `author_profile` publishes a
  library package only — it does not put tracks or fields on the app.
  Equally incomplete: tracks/fields without **views** and **demo seed
  entries** — always add those in the same batch for demo + validation.
- **Build before propose.** Never call `integral_begin_batch` /
  `integral_author_profile` / `integral_create_app` on a greenfield turn
  before `integral_propose_design` has minted the design card and the user
  has replied. `author_profile`-only batches are also refused without a
  design marker.
- **Re-propose on affirm.** After the user affirms an unapproved design with
  no new shape requests, never call `integral_propose_design` again. That
  stalls the build (begin_batch without commit) and leaves no WRITE · BATCH
  card. Affirm → begin_batch → creates → commit_batch only.
- Calling create/apply tools **without** an open batch for a multi-step scaffold —
  that floods the user with one card per step instead of one plan to bless. The
  backend refuses create/apply staging while a design proposal is open unless a
  batch is open (`batch_required`).
- Creating a track **without** `app_id` (standalone track create) — use
  **`integral_create_app_track`** with `app_id="{{app.id}}"` so tracks attach
  to the app created in the same batch. A bare track create without `app_id`
  mints a standalone "App: (no app)" card and blocks the turn.
- **Staging `create_app` outside the batch** (a separate card) while the tracks use
  `{{app.id}}`. The token only resolves to an app created **in the same batch**, so
  `create_app` MUST be the first op after `begin_batch`. A standalone app card and a
  separate tracks batch leaves `{{app.id}}` unresolved and the track creates fail.
- **Authoring a profile from scratch** when `integral_list_profiles` shows a
  library package that fits — always prefer `integral_apply_profile_to_track`.
- Creating a **duplicate app** when `integral_list_apps` already shows one for the
  intent — extend it instead.
- **Spraying a track across apps.** `integral_create_app_track` targets exactly
  ONE app. For a track in the app this batch is creating, use
  `app_id={{app.id}}`. For an EXISTING app, resolve its id ONCE by exact-name match
  against `integral_list_apps` and reuse that single id. If the name matches zero
  or multiple apps, **STOP and ask** — never propose the track against several apps
  "to be safe", and never use a guessed or placeholder id.
- Proposing a `view_type` or field type not confirmed by
  `integral_describe_substrate`.
- Designing deep schema inline — mixed-type anchored tracks, expansion/anchor
  relations (`target: "track"`), or a multi-relation graph — is `integral_model`'s
  job; hand it off. (A single simple cross-track lookup relation is fine inline,
  with the required `allow_cross_track` + `target_track_types` shape.)
- Declaring a cross-track relation field with a bare `target_entry_types` and no
  `allow_cross_track: true` + `target_track_types` — it is rejected at create.
- **Skipping demo seeds or views on greenfield.** Every new-app batch must
  include `integral_save_view` (≥1 per track) and `integral_create_entry`
  (2–4 demo seeds per track) unless the user explicitly asked for empty.
  An app with tracks/fields but no views/entries is not demo-ready and cannot
  be fidelity-checked.
- Over-seeding with realistic-looking private data (fake SSNs, real-looking
  emails). Demo labels only. Skip seeds entirely only when the user asked for
  an empty structure.
- **Creating an app, track, or entry type with a blank or missing `description`**
  when the tool accepts one — every named object gets a specific one-line purpose.
- Claiming the scaffold "exists", "is being set up", or "created" before the
  batch's staging-resolved marker fires (`state=consumed`).
- Opening or expecting a Prompt Sheet Approve on the **design** beat — design
  confirmation is chat-only; Approve is only for the post-`commit_batch` build.

## Example walkthrough

> **User:** "Set me up a simple CRM."

**— Turn 1: propose, then STOP —**

1. `integral_whoami` → confirm acting user + active workspace.
2. `integral_list_apps` → no CRM app present.
3. `integral_list_profiles(type_hint="crm")` → a library "CRM" package exists.
4. `integral_describe_substrate` → confirm `kanban` + `table` are valid view keys.
5. Call `integral_propose_design` with:
   - `summary="CRM app: Contacts + Deals, CRM package, Pipeline board"`
   - `proposal` = the full shape (tracks + key fields + views + package choice),
     e.g. "**CRM** app — applying the CRM library package.\n\n- **Contacts** —
     people/orgs (name, email, company)\n- **Deals** — pipeline (amount, stage,
     contact relation)\n- Views: Contacts table, Deals Pipeline kanban\n\nSound
     right?"
6. **End the turn here — do NOT open the batch yet.** Wait for the user to
   confirm. Further tools are refused until they reply.

**— Turn 2: after the user says "yes" — build (do NOT call
`integral_propose_design` again) —**

7. `integral_begin_batch(label="Set up CRM")`.
8. `integral_create_app(name="CRM", description="Contacts and the deals you're working with them.")`.
   _(Reference each track by its named token `{{track.id:<name>}}`, so views/entries land on the right track regardless of order.)_
9. `integral_create_app_track(app_id="{{app.id}}", name="Contacts", description="People and organizations you do business with.")`.
10. `integral_apply_profile_to_track(track_id="{{track.id:Contacts}}", profile_template_id=<crm pkg>)`.
11. `integral_save_view(track_id="{{track.id:Contacts}}", name="All Contacts", view_type="table", config={})`.
12. `integral_create_entry(track_id="{{track.id:Contacts}}", title="Example Contact", text="…")` ×2 seeds.
13. `integral_create_app_track(app_id="{{app.id}}", name="Deals", description="Open and closed sales opportunities and their stage.")`.
14. `integral_apply_profile_to_track(track_id="{{track.id:Deals}}", profile_template_id=<crm pkg>)`.
15. `integral_save_view(track_id="{{track.id:Deals}}", name="Pipeline", view_type="kanban", config={group_by:"stage"})`.
16. `integral_commit_batch(summary="CRM app: Contacts + Deals, CRM profile,
    Pipeline board, 2 sample contacts.")`.
17. Reply: "Staged a **CRM** app with Contacts + Deals tracks, a Pipeline board, and
    two sample contacts — approve the Prompt Sheet card to build it." **Wait
    for that Approve.** Do not say it is set up yet.

> The `{{app.id}}` / `{{track.id:<name>}}` / `{{entry.id:<title>}}` tokens are resolved
> to the real ids at approval time (the object created earlier in the same batch). Use
> them verbatim — never substitute a guessed id.

If the user then wants deals to reference contacts, or a Project→Tasks expansion,
hand that to `integral_model` — that is relation design, not scaffolding.

### Example — underspecified request (clarify + propose first)

> **User:** "I need something to track my rental properties and tenants."

This is underspecified — no tracks, fields, or views were named. Do NOT go
straight to `integral_begin_batch`.

**— Turn 1: propose, then STOP —**

1. `integral_whoami` / `integral_list_apps` / `integral_list_profiles` —
   grounding reads as usual; nothing existing fits.
2. Nothing here materially changes the shape of a rental-tracking app, so
   skip clarifying questions and go straight to proposing — **once**, with
   the full design **only** in `proposal` (not repeated in chat prose):
   `integral_propose_design(
     summary="Rentals app: Properties + Tenants, linked",
     proposal="**Properties** track (address, type, units, notes) and a
     **Tenants** track (name, contact info, unit, lease dates), linked so
     each tenant points at their unit. Default table + status views."
   )`.
   Optional short closer only: "Here's a shape — confirm or correct it."
3. **End the turn — wait for the user's reply.** Further tools are refused.

**— Turn 2: after the user says "yes" — build —**

4. Go **straight to the batch procedure** — `integral_begin_batch`,
   `integral_create_app`, `integral_create_app_track` ×2, entry types inline,
   views, seeds, `integral_commit_batch` — exactly as in the walkthrough
   above. **Do NOT call `integral_propose_design` again**; the call in step 2
   already satisfied the gate, so `integral_commit_batch` passes. (Had you
   skipped proposing entirely, the commit would be rejected as
   `design_not_proposed`.)
