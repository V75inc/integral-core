---

name: integral_workspace
description: Reads and manages the user's Integral apps and tracks — create, update, delete, scope, access, and collaboration.
spec: jv
allowed-tools:
  - integral_whoami
  - integral_get_scope
  - integral_list_workspaces
  - integral_list_apps
  - integral_get_app
  - integral_list_workspace_tools
  - integral_call_workspace_tool
  - integral_invoke_app_operation
  - integral_list_tracks
  - integral_get_track_schema
  - integral_resolve_entry
  - integral_get_access
  - integral_list_share_links
  - integral_create_track
  - integral_update_track
  - integral_delete_track
  - integral_create_app
  - integral_create_app_track
  - integral_update_app
  - integral_delete_app
  # Grant a collaborator access to an app/track/entry (collaboration mgmt).
  - integral_share
  - integral_add_collaborator
  - integral_remove_collaborator
  - integral_invite
  # Share-link lifecycle (mint/revoke) and per-resource access exclusions.
  - integral_mint_share_link
  - integral_revoke_share_link
  - integral_set_exclusion
  - integral_remove_exclusion
  # Connector sync administration — surface conflicts, resolve, re-sync.
  - integral_list_conflicts
  - integral_resolve_conflict
  - integral_trigger_sync
# requires-actions (jvagent skill standard): the Action type whose get_tools()
# furnishes every integral_* tool this SOP coordinates.
requires-actions:
  - EmbeddedIntegralAction
extends: action:integral/embedded_integral_action
tags:
  - integral
  - workspace
  - apps
  - tracks
  - crud
---

# Integral workspace — SOP

## Orientation — "what apps / tracks do I have"

Before any structure or sharing work, this skill also answers the flat
orientation question: "what apps do I have", "list my tracks", "what's in
this workspace", "show the tracks under <app>". This is the read-only map
of the containers in the active workspace.

- `integral_list_apps` — apps the caller can see (an App groups tracks —
  a schema/database holding tables). Each row carries the caller's
  `role`. Permission-filtered.
- `integral_list_tracks` — tracks the caller can see (a Track is a table;
  its entries are records). Pass `app_id` to scope to one app.

Active-workspace scope is bound from the session header
(`X-Integral-Scope`), never a tool argument — every list reflects the
caller's one active workspace and cannot reach into another.

> **Not-yet-available:** `integral_get_scope` (a single scope probe) and
> `integral_list_workspaces` (a cross-workspace lister) are specified in
> the tool manifest but are not yet dispatchable. Until they ship, derive
> "what's my scope" from `integral_whoami` (see `integral_identity`) plus
> `integral_list_apps` / `integral_list_tracks`. Do not call these names.

A plain orientation question overlaps with `integral_identity`; either
skill may answer it. Reach for **this** skill when the orientation read
is the prelude to a structure or sharing mutation below.

## Workflow

1. Determine the intent: **structure** (their apps / tracks), **content**
   inside a track, or **access/sharing** (granting someone access to an
   app/track/entry). Use this skill for structure and sharing; delegate
   content work to `integral_entries`. For a "share X with <person>"
   request, first resolve the resource's id (via `integral_list_apps` /
   `integral_list_tracks`), then use `integral_share` (see Mutations).
2. Default discovery flow when the user gives no specific id:
   1. `integral_list_apps` — surface available apps.
   2. `integral_list_tracks` — narrow by `app_id` if supplied.
   When you name apps or tracks in your reply, **link every one** using
   each row's `action_url` (or `/apps/{id}` / `/tracks/{id}`). Plain
   titles alone are forbidden — see `integral_navigation`.
3. Call `integral_get_app` only when you need fields beyond the list
   summary (membership, settings, content profile, etc.). Call
   `integral_get_track_schema` when you need a track's shape — its
   EntryTypes, fields, and Views — e.g. before composing entries for
   it or describing what it holds.

### Greenfield apps (whole domains)

If the user wants a **whole new working area** (app + several tracks + shape), do **not** mint one `integral_create_app` card and stop — hand off to **`integral_scaffold`** (outline in chat → chat confirm → batch build, auto-apply when chat-affirmed). Use this skill for single-resource creates/updates on an existing structure.

### Mutations — propose, the user blesses

All track mutations are **propose** tools: you call a single tool, it
**stages** a change the user approves in Integral. There is **no
separate execute step** — when the user blesses the staged change in
the chat surface, the backend applies it. Your job is to call the
propose tool and present the staged card; you never apply the change
yourself.

- **Create a track** — call `integral_create_track` with `name` and a
  concise one-line `description` (the track's purpose — never leave it
  blank), plus optionally `app_id` to identify the App the track belongs
  to, or `visibility`. Workspace scope is bound via the active session
  header, not a tool arg. It stages a create the user approves in Integral.
- **Create an app** — call `integral_create_app` with `name` and a
  specific one-line `description` (never blank) when the user wants a whole
  new app (schema/database) rather than another track inside an existing one.
  It stages an app create the user approves in Integral.
- **Create a track inside an app** — call `integral_create_app_track`
  with `app_id`, `name`, and a concise `description` when the new track belongs to a known app.
  Prefer this over a bare `integral_create_track` when the user is
  clearly working inside one app. It stages the track create the user
  approves in Integral.
  - **Resolve the target app to exactly ONE id first.** Call
    `integral_list_apps` and match the named app by exact name; use that
    single `app_id` for every track. **Never** create the same track in
    more than one app, and never propose it against several apps "to be
    safe". If the name matches zero or multiple apps — or the app was just
    created and is not in the list yet — STOP and ask which app, rather than
    guessing an id or fanning out. (A freshly-created app is only reachable
    by a real id after its create is blessed; within a single scaffold batch,
    reference it as `{{app.id}}` instead — see `integral_scaffold`.)
- **Update a track** — call `integral_update_track` with `track_id`
  and an `updates` object carrying only the fields the user wants
  changed. It stages an update the user approves in Integral.
- **Delete a track** — call `integral_delete_track` with `track_id`.
  The approval card explicitly states that deletion cascades to
  entries and is irreversible; the agent should not soften that
  warning. It stages a delete the user approves in Integral.
- **Delete an app** — call `integral_delete_app` with `app_id` when the
  user explicitly asks to delete the WHOLE app, not just one track
  inside it. The approval card states that deletion cascades to every
  track the app contains (and their entries) and is irreversible; a
  track shared with another app is unlinked from this app only, not
  deleted. Confirm the user really means the whole app before staging —
  if they only want one track gone, use `integral_delete_track` instead.
- **Share a resource** — call `integral_share` to grant a collaborator
  access. Required: `resource_type` (`app` / `track` / `entry`) and
  `resource_id`; plus the collaborator's `email` (or
  `collaborator_user_id`). Optional `role` (`viewer` / `commenter` /
  `editor`, default `commenter`). It stages a collaborator grant the
  user approves; the email→user resolution happens at bless. The grant
  is always for ANOTHER user — identity of the acting principal is never
  a tool arg.

### Staging discipline

- A propose tool returns a staged-change envelope; it does **not**
  apply the change. Present it and wait — do not claim the create /
  update / delete succeeded until the user has blessed it.
- The `_kind == "staged_change"` sentinel in the result tells the chat
  surface to render an approval card; in that turn your job is **just**
  to present and wait. Once the user blesses it, Integral applies the
  change (the resident threads the originating `interaction_id` so the
  `[SYSTEM:STAGING-RESOLVED]` closure marker fires on the right turn).
- If a propose call returns an error envelope, surface it verbatim —
  never retry blindly or pretend a write happened.

## Sharing & access — who can see a resource

Granting access is **privacy-critical** — a mistake here exposes data.
Treat every share as a propose-and-bless mutation, and **read the access
state before you change it.**

### When to use this

- "Share this track with jane@…", "give Bob editor access to the CRM
  app", "let the team see this entry", "who has access to this track?",
  "what's shared on this app?".

### When NOT to use → delegate

- **The resource's own content** (its entries, comments) → `integral_entries`.
- **Inviting someone who isn't yet a workspace member at the workspace
  level** (vs. sharing one resource) → a workspace invitation flow; the
  resource-level `integral_share` here auto-grants a *guest* membership on
  a cross-workspace share, which covers most "let this person in" asks.

### Grounding — read access before you mutate

Before proposing any share, know the current access picture so you do not
duplicate a grant or widen scope unintentionally:

1. Resolve the resource id first — `integral_list_apps` /
   `integral_list_tracks` (or `integral_resolve_entry` via
   `integral_entries`) for an entry.
2. Inspect current access. A unified access snapshot (`integral_get_access`
   — direct collaborators, inherited, excluded, links, `effective_total`)
   and a share-link lister (`integral_list_share_links`) are specified in
   the tool manifest but are **not yet dispatchable** (status: gap). Until
   they ship, ground from what the user states and the resource listing;
   do **not** call those names, and do not claim to have read an access
   list you could not fetch.

### Procedure — share a resource

`integral_share` adds a person as a **direct collaborator** at a role:

- Required: `resource_type` (`app` / `track` / `entry`) and `resource_id`.
- Identify the person by `email` (or `collaborator_user_id`). The
  email→user resolution happens at bless.
- `role` is capped to `viewer` / `commenter` / `editor` — `owner` /
  `admin` are rejected by the tool, and it defaults to `commenter` when
  unspecified. Do not promise a higher grant than the tool can mint.
- The grant is always for ANOTHER user; the acting principal's identity is
  never a tool argument.
- A cross-workspace share auto-grants the recipient a *guest* workspace
  membership — mention this when it applies so the user isn't surprised.

It **stages** a collaborator grant the user blesses in Integral (no
separate execute step). Present the staged card and wait.

### Staging & forbidden patterns (sharing)

- Never say "shared" / "granted" until the card resolves
  (`[SYSTEM:STAGING-RESOLVED] … state=consumed`).
- Never grant `owner` / `admin` via `integral_share` — the tool rejects
  it; don't claim otherwise.
- Never widen scope to a workspace the caller isn't in — scope is bound by
  the session, not a tool arg.
- Don't fabricate an access list. If you couldn't read current access
  (the access tools aren't live yet), say what you're about to grant
  rather than asserting who already has it.

## Scope

This skill covers apps, tracks, and resource sharing/access. It does
**not** create entries, manage comments, or edit content profiles. For
entries see `integral_entries`. For identity / flat orientation see
`integral_identity`. For activity rollups see `integral_insights`.

## Grounding

- Only mention apps or tracks the API has actually returned in the
  current turn — never recall ids from prior conversations without
  re-reading.
- Surface error envelopes
  (`{"error": true, "error_code": ..., "message": ...}`) verbatim.
- Never invent `app_id` or `track_id` values; if a needed id is
  missing, ask the user or list-and-confirm first.

## Example

> **User:** "Create a Feedback track under the CRM app and share it with
> bob@example.com as an editor."

1. `integral_list_apps` → match "CRM" by name; take its `app_id` verbatim
   (exactly one app — never fan out to multiple apps).
2. `integral_create_app_track(app_id=<crm id>, name="Feedback", description="Customer feedback and feature requests.")` → stage
   the track create; present the card and **wait** for bless.
3. After `[SYSTEM:STAGING-RESOLVED] … state=consumed`, re-read
   `integral_list_tracks(app_id=<crm id>)` → obtain the new track's id.
4. `integral_share(resource_type="track", resource_id=<feedback track id>,
   email="bob@example.com", role="editor")` → stage the collaborator
   grant; present and **wait** again.
5. Only after the share card resolves may you say Bob has editor access.
   Never claim "shared" or "created" while cards show AWAITING APPROVAL.
