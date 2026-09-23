---


name: integral_models
description: "Inspects, authors, and modifies Integral Operational Models — the schema layer defining a track or app's EntryTypes, Tags, and Views. Use when the user asks about profile structure, draft/publish lifecycle, or library merges."
spec: jv
allowed-tools:
  - integral_list_models
  - integral_describe_model
  # Substrate introspection (the field-type / view-type palette + signed
  # plugins) and per-track customization recommendations — consult these
  # before authoring or modifying so proposals reference real, supported
  # building blocks.
  - integral_describe_substrate
  - integral_recommend_customizations
  - integral_author_model
  - integral_modify_model
  - integral_apply_model_to_track
  - integral_get_model_draft
  - integral_diff_model_draft
  - integral_propose_model_revision
  - integral_publish_model_draft
  - integral_discard_model_draft
  - integral_draft_new_model
# requires-actions (jvagent skill standard): the Action type whose get_tools()
# furnishes every integral_* tool this SOP coordinates.
requires-actions:
  - EmbeddedIntegralAction
extends: action:integral/embedded_integral_action
tags:
  - integral
  - operational_models
  - schema
  - authoring

---

# Integral operational models — SOP

This skill is for **substrate authoring** — shaping the schema a track
or app exposes (its EntryTypes, Tags, Views), or composing a new
library Operational Model that other tracks can pick up.

## When to use

The user wants to **inspect, attach, merge, or reshape** a track or app's
Operational Model — its EntryTypes, Tags, Views, or library packages. Typical
asks:

- "What entry types does this track have?"
- "Add a Status field and a kanban view to Deals."
- "Apply the CRM library profile to this track."
- "Publish the profile changes we drafted."

Routine entry work belongs in skill `integral_entries`; track/app lifecycle
belongs in skill `integral_workspace`.

## When NOT to use — delegate

- **Creating or editing individual entries** → skill `integral_entries`.
- **Standing up a new track or app** → skill `integral_workspace` or
  `integral_scaffold`.
- **Domain-modeling judgment** (lookup vs anchor, mixed entry types) →
  skill `integral_model`.
- **Analytics rollups** → skill `integral_insights`.

## Grounding (read before write)

- Only mention library profiles, EntryTypes, Tags, or Views the API
  has actually returned in the current turn — never recall ids from
  prior conversations without re-reading.
- Never invent `model_template_id`, `draft_id`, `entry_type_id`,
  `view_id`, or `tag_id` values. If a needed id is missing,
  list-and-confirm first (read it via `integral_list_models` /
  `integral_describe_model` / `integral_get_model_draft`).
- Always read `integral_describe_substrate` before authoring or modifying
  so every EntryType / field / View you propose references a real,
  supported type — never invent a field/view type the substrate cannot
  materialize.
- Always read `integral_describe_model` (or `integral_get_model_draft`)
  before proposing modifications — never modify a profile you have not
  inspected this turn.
- Describe profile shape using the user's vocabulary, not jvspatial
  jargon: say "a field for priority" rather than "an EntryType form
  schema with a select-typed field keyed `priority`."

## Procedure

### Reads (no confirmation needed)

- `integral_list_models` — surface available library packages. Pass
  `type_hint` to bias the listing toward a kind of profile (e.g.
  `'crm'`, `'tasks'`); omit it to list all accessible packages.
- `integral_describe_model` — describe the profile currently
  attached to a track or app: its EntryTypes, Tags, Views, and any
  in-flight draft state. Pass `track_id` (or `space_id` for an app) —
  exactly one.
- `integral_get_model_draft` — fetch (idempotently auto-creating)
  the editable draft for a profile via `operational_model_id`. It
  returns the `draft_id` the precise-revision lifecycle works against,
  so the user keeps reviewing before anything goes live.
- `integral_diff_model_draft` — structural diff of a draft
  (`draft_id`) vs the published profile, plus a per-track entry-impact
  summary. Read this before publishing so you can tell the user exactly
  what changes and which entries are affected.
- `integral_describe_substrate` — the substrate's available building
  blocks: the field-type and view-type palette plus any signed code
  plugins.
- `integral_recommend_customizations` — suggested profile tweaks for a
  track, derived from its entries and usage. A starting point for a
  `modify`/`author` proposal; still inspect with `describe_operational_model` and
  let the user bless the resulting staged change.

### Authoring & modifying (propose, the user blesses)

Profile mutations are **propose** tools: you call a single tool, it
**stages** a change the user approves in Integral. There is **no
separate execute step** — when the user blesses the staged change in
the chat surface, the backend applies it (for `publish`, the bless
runs the atomic draft→published swap and any migrations). These are the
most consequential writes the agent can make — they reshape what every
entry in a track has to conform to. Be deliberate; present the staged
card and wait.

- **Apply an existing library profile.** Almost always preferable to
  authoring something new. Call `integral_apply_model_to_track` with
  `model_template_id` (the library package) and `track_id` (the
  target track). It stages an additive attach/merge the user approves
  in Integral.
- **Modify an attached profile (one discrete change).** For a single
  add/remove on the profile attached to a track or app — add or remove
  one EntryType, View, or Tag. Call `integral_modify_model` with
  `action` (one of `add_entry_type`, `remove_entry_type`, `add_view`,
  `remove_view`, `add_tag`, `remove_tag`) plus `track_id` **or**
  `app_id` to name the target, and the kwargs that action needs
  (`name` / `icon` for an EntryType; `view_type` / `config` for a View;
  `name` / `color` / `group_key` for a Tag; `entry_type_id` / `view_id`
  / `tag_id` to remove one). One call stages one change the user
  approves in Integral; chain calls for several edits.
- **Revise a draft precisely (batch).** When you want explicit control
  over several patch operations at once, call
  `integral_propose_model_revision` with `draft_id` and an
  `operations` list of patch-DSL ops (`add_entry_type`, `add_field`,
  `add_view`, `add_relation`, …). It stages the revision the user
  approves in Integral.
- **Draft a new library profile.** Call `integral_draft_new_model`
  only when the user explicitly asks for a standalone reusable package;
  use `profile_name` (optionally `scope` `"track"`/`"app"` and a
  `description`) to start an empty package draft you then populate via
  revision ops. It stages the new draft the user approves in Integral.
- **Author a library profile from a description.** Use
  `integral_author_model` only when the user explicitly asks for a
  standalone reusable package. It creates a library artifact; it does
  **not** change an existing app or track. When the request names an
  existing app/track, revise that resource's attached profile instead.
- **Publish the draft.** Once the diff looks right and the user agrees,
  call `integral_publish_model_draft` with `draft_id`. It stages the
  publish; when the user blesses it, Integral runs the atomic
  draft→published swap (including migrations).
- **Discard a draft.** Call `integral_discard_model_draft` with
  `draft_id` to throw away an unpublished draft the user no longer
  wants (the published parent is untouched). It stages the discard the
  user approves in Integral.

### Decision flow

1. User asks to set up or reshape something.
2. List library profiles relevant to the user's description with
   `integral_list_models`, and `integral_describe_model` the target
   to see what's already attached.
3. If a good fit exists → propose `integral_apply_model_to_track` for
   the target track.
4. If a fit exists but needs tweaks → propose
   `integral_apply_model_to_track` first, then layer each tweak on:
   `integral_modify_model` for a discrete add/remove of one
   EntryType/View/Tag (by `action` + `track_id`/`app_id`), or
   `integral_propose_model_revision` for a batch of patch ops against
   a `draft_id`.
5. **Before synthesizing anything new, re-check the target.** If the
   user named an existing app or track, its attached profile is the
   target: call `integral_get_model_draft`, revise it with
   `integral_propose_model_revision`, then diff and publish it. Never
   call `integral_author_model` or `integral_draft_new_model` for
   that request, even when no library package fits — those create a
   detached library artifact and do not satisfy an existing-resource
   change. If there is no existing track/app being reshaped at all — the
   user wants a whole new working area, not a schema tweak on something
   that exists — STOP and hand off to `integral_scaffold` (or
   `integral_workspace` for a single bare track/app) instead of handling
   it here. A new library profile is appropriate only when the user
   explicitly asks for a standalone reusable package.
   - **If the request was underspecified** (no concrete entry types/
     fields named), do not synthesize immediately: state the planned
     EntryTypes and key fields in chat prose and get an explicit
     go-ahead first — same pattern `integral_scaffold` and
     `integral_onboard` use. Only after affirmation call
     `integral_author_model` / `integral_draft_new_model`.
   Once attached, iterate the draft, `integral_diff_model_draft` to
   confirm impact, and `integral_publish_model_draft` to go live.
   Each propose is its own staged change the user blesses — that's
   correct; the user is blessing distinct operations.

## Staging discipline

- A propose tool returns a staged-change envelope; it does **not**
  apply the change. Present it and wait — do not claim the profile was
  modified or published until the user has blessed it. For publish in
  particular, the draft→published swap and migrations run only on the
  bless, never on the propose call.
- The `_kind == "staged_change"` sentinel in the result tells the chat
  surface to render an approval card; in that turn your job is **just**
  to present and wait. Once the user blesses it, Integral applies the
  change (the resident threads the originating `interaction_id` so the
  `[SYSTEM:STAGING-RESOLVED]` closure marker fires on the right turn).
- If a propose call returns an error envelope, surface it verbatim —
  never retry blindly or pretend a write happened.

## Forbidden patterns

- Modifying or publishing a profile you did **not** read this turn with
  `integral_describe_model` / `integral_get_model_draft`.
- Proposing a field type or view type **not** confirmed by
  `integral_describe_substrate`.
- Inventing `model_template_id`, `draft_id`, `entry_type_id`,
  `view_id`, or `tag_id` values — list-and-confirm first.
- Claiming a profile was modified, published, or discarded before the
  user blesses the staged card.
- Publishing a draft without showing the user the
  `integral_diff_model_draft` impact first.
- Authoring a brand-new library package when an existing one fits —
  prefer `integral_apply_model_to_track` first.
- Synthesizing a brand-new library package (`integral_author_model`)
  or starting an empty draft (`integral_draft_new_model`) before
  proposing the planned EntryTypes/fields in prose and getting an
  explicit go-ahead, when the request was underspecified.
- Handling a "stand up a whole new app/domain" request here instead of
  delegating to `integral_scaffold`/`integral_workspace` — see "When NOT
  to use" above.

## Example

> **User:** "Add a Priority select field and a kanban view to our Deals
> track, then publish it."

1. `integral_describe_substrate` → confirm `select` field type and
   `kanban` view type are supported.
2. `integral_describe_model(track_id=<deals>)` → read the attached
   profile's `operational_model_id`, existing entry types, and any draft
   state.
3. `integral_get_model_draft(operational_model_id=<from step 2>)` →
   obtain `draft_id` for the precise-revision lifecycle.
4. `integral_propose_model_revision(draft_id=<draft_id>, operations=[
     {op: "add_field", entry_type: "deal",
      spec: {key: "priority", name: "Priority", type: "select",
             options: ["Low", "Medium", "High"]}},
     {op: "add_view", spec: {view_type: "kanban", name: "Board",
      config: {group_by: "priority"}}}
   ])` → stage the batch revision; present the card and **wait**.
5. After the user blesses the revision, `integral_diff_model_draft(
   draft_id=<draft_id>)` → explain structural changes and how many
   existing deals are affected.
6. `integral_publish_model_draft(draft_id=<draft_id>)` → stage the
   publish; present and **wait** again. Only after
   `[SYSTEM:STAGING-RESOLVED] … state=consumed` may you say the profile
   is live.

### Example — underspecified, and not actually this skill's job

> **User:** "I need something to track my rental properties and tenants."

Nothing exists yet to reshape — this is a "stand up a new working area"
request, which belongs to `integral_scaffold` (or `integral_workspace`
for a single bare track), not here. Say so and hand off, rather than
calling `integral_author_model` directly:

> "That sounds like a new app rather than a tweak to something existing
> — let me set that up for you." → hand off to the `integral_scaffold`
> pattern (clarify if needed, propose the shape, then batch-build).

Only reach for `integral_author_model`/`integral_draft_new_model` in
this skill when you are genuinely reshaping or extending something that
already exists, or the user explicitly wants a standalone reusable
library package (not a new working area) — and even then, per the
decision flow above, propose the shape in prose first if the request was
underspecified.
