---
name: integral_scaffold
description: "Owns operational app delivery from a business need: guide design, batch the approved schema, relations, views, operating skills and reminders, then verify the applied result. Use for new apps and continuing or repairing their builds; retain ownership while consulting modeling and scheduling skills."
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
  - integral_list_tracks
  - integral_get_track_schema
  - integral_query_entries
  - integral_list_views
  - integral_schedule_task
  - integral_list_routines
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

# Operational app delivery

## When to use

A user describes an operational need: "I need an app to manage my car rental
business", "manage repairs", "track inventory", or "build the design we agreed".
Own the outcome through **discover → design → stage → approve → verify → handoff**.
A created App node is not a completed app. Every requested capability needs an
implementation and observable acceptance evidence.

## When NOT to use — delegate

Existing-record CRUD belongs to skill integral_entries; existing-schema changes
to skill integral_model; library lifecycle to skill integral_profiles; routine-only
requests to skill integral_scheduling. For a new app, consult those skills as
specialists but **retain delivery ownership**. Multiple relations are normal;
do not bounce the user between scaffold and model or stop at an empty skeleton.

## Grounding (read before write)

1. `integral_whoami`: confirm identity and active workspace.
2. `integral_list_apps`: resolve an existing app before creating anything. Use
   exact returned ids. If a previous attempt partially built it, inspect its
   tracks with `integral_list_tracks` and continue the missing work.
3. `integral_list_profiles`: inspect matching packages and their scope. A track
   package can shape a track; an app package is not a track template. Prefer an
   applicable package, but never apply an app-wide package to every track.
4. `integral_describe_substrate`: inspect available field/view types and their
   configuration. Use the current tool schemas, not guessed parameters.
5. For reminders, `integral_list_routines`: avoid duplicate schedules. Establish
   the user's IANA timezone and reminder cadence; do not guess their location.

Resolve existing ids from tools. New objects use exact batch tokens:
`{{app.id}}`, `{{track.id:Cars}}`, `{{entry.id:Demo Car A}}`. A token can only
reference an object created **earlier in the same batch**. Never fabricate ids
such as `n.Track.Cars`, use a display name as an id, or use bare `{{track.id}}`
when several tracks exist. Give named objects unique names within the build.

## Procedure

### 1. Guide the design

Translate each user need into data, relationships, daily operations, views, and
scheduled behavior. Track ≈ table, Entry ≈ record, App ≈ database. Use lookup
relations for independently managed records; use anchored detail tracks only
when a parent needs its own collection and access boundary (skill integral_model).

Ask only questions that change the operational result, using `integral_ask_user`
for genuine choices. Offer useful defaults in the design. Explicitly distinguish
manual status changes, agent-guided procedures, and enforced automatic rules.
A select field does not enforce no-double-booking; a date field does not notify.
If the substrate cannot enforce a required rule, say so in the design and agree
the supported alternative. Never silently downgrade a requirement.

Call `integral_propose_design` with the full design in `proposal`, including:
- App and each track's purpose, entry type, key fields, and lookup relations.
- Views with the decisions they help the user make.
- Operating procedures needed to keep data consistent.
- Reminders: dates checked, lead window, cadence, timezone, and delivery here
  in this chat. These are personal routines, not team-wide notifications.
- Demo records versus an explicitly requested empty app.
- A short acceptance checklist mapping every requested capability to a check.

This card is the **one design surface**. End the turn and let the user confirm
or correct. Do not repeat the full proposal in chat. A correction changes the
design; an affirmation advances to building without another proposal.

### 2. Build the confirmed design

After confirmation, activate this skill and finish the build. Do not ask for a
second design confirmation. Keep a checklist of all agreed capabilities.

1. `integral_begin_batch` once. A re-enter keeps previous ops; it does not reset.
2. `integral_create_app` first, with a specific description. Extend an existing
   app by its actual id instead when recovering or expanding.
3. `integral_create_app_track` for **every** planned track, app_id=`{{app.id}}`,
   concise description, and inline `entry_types` including fields. Or apply a
   verified track package using `integral_apply_profile_to_track`. A standalone
   `integral_author_profile` creates a library package, not an attached schema.
4. `integral_save_view` for **each** track. Set a useful default table; add a
   board/calendar only with the real field keys and supported configuration.
5. `integral_create_entry` for demo records unless the user requested empty.
   Always supply the intended `entry_type` and structured `fields`, not just
   prose. Create referenced demo records first and dependent records last.
   Use clearly fictional labels and no realistic personal data. Aim for two
   contrasting records per track (e.g. available vs rented), not volume.
6. `integral_author_skill` for the app's agreed operating procedures. Pass
   app_id=`{{app.id}}`, a useful discovery description, `tools_required`, and
   `body_override`. Keep private app scope by default. Use the seven sections:
   When to use; When NOT to use; Grounding; Procedure; Staging discipline;
   Forbidden patterns; Example. The procedure must read current schemas and
   records, validate its preconditions, stage the related writes as a batch,
   then verify after approval. An authored SOP is agent-guided behavior,
   **not** a database constraint or background event handler.
7. `integral_schedule_task` for agreed reminders **in the same batch**, after
   all referenced tracks exist. Consult skill integral_scheduling. Supply
   cron + IANA timezone and a self-contained instruction with named batch
   tokens for the tracks. Query current dates each run, omit blank dates,
   include overdue records, and link actionable results. Keep read-only
   reminders free of write_scope. Do not put cadence or "schedule a reminder"
   inside the replay instruction. If cadence/timezone is still unknown, ask
   before staging; never promise the reminder is complete without a routine.
8. Compare the operations against the acceptance checklist, then
   `integral_commit_batch`. For an explicitly empty app only, pass
   `allow_empty=true`; schema and views remain mandatory.

**Inline lookup shape** (one for each relationship, any number per track):
```json
{"key":"car","name":"Car","type":"relation","relation":{
  "target":"entry","target_track_types":["cars"],
  "target_entry_types":["car"],"allow_cross_track":true,"many":false}}
```
The config must be nested under `relation`. Track/type names must match the
actual target names after slug normalization. A Rental can have **both** car
and renter lookups; that does not require separate modeling or build cycles.
Fields use `{key, name, type, enum?}`; inspect substrate for advanced shapes.

### 3. Recover without duplication

- `batch_incomplete` / `ready:false`: no approval exists. Append every missing
  item to the **same open batch**, then commit again. Do not end with "ready".
- Invalid schema/tool arguments: correct the input using the error and live
  contract. Do not blindly repeat an identical failing call.
- Invalid references/order in already appended ops: cancel the **unapproved**
  batch using `integral_cancel_batch`, rebuild the corrected operations in
  dependency order, then commit. Never leave two build cards for one design.
- Partial execution: inspect the reported completed steps and actual app.
  Re-approval of a retryable batch resumes at the failed operation; it must
  not recreate earlier objects. If inputs need changing, cancel/revoke the
  failed approval in the UI before staging a repair of only the missing work.
- User rejection means stop; do not secretly rebuild or reinterpret as approval.

### 4. Verify and hand off

After `[SYSTEM:STAGING-RESOLVED] ... state=consumed`, inspect actual state:
`integral_list_apps`, `integral_list_tracks`, `integral_get_track_schema`,
`integral_list_views`, `integral_query_entries`, and `integral_list_routines`
for scheduled work. Use returned ids, never old placeholders.

Verify every designed track is attached to the right app, its fields and select
options exist, each view targets the right track, seeded relation values resolve
to the intended records, and the routine is active with the correct timezone
and next run. Check the batch's author_skill results for procedure creation.
If a capability is missing, repair it with a scoped batch and verify again.
Do not claim a routine has already fired merely because it is active.

Return a link to the app, a brief operating guide, and the reminder cadence.
State any remaining limitation plainly. "Built" requires actual readback;
"staged" requires a successful commit token. No token means no approval card.

## Staging discipline

Design confirmation happens in chat; the build is applied by the Prompt Sheet
approval. Propose tools only accumulate operations while a batch is open.
Wait for successful consumption before claiming creation, and read back before
claiming the complete operational need is satisfied. Never execute around the
approval path. A sequential batch is resumable, not an atomic transaction.

## Forbidden patterns

- Ending after a skeleton, ignoring reminders, or treating date fields as alerts.
- Losing ownership through scaffold ↔ model delegation loops.
- Creating duplicate apps/tracks when recovering from a partial build.
- Inventing ids, unsupported field/view types, or forward batch references.
- Calling the same invalid tool repeatedly or claiming success after an error.
- Spreading mutations over standalone cards instead of the build batch.
- Omitting views or field-bearing schemas on any track.
- Promising enforced booking exclusion from a skill or manual status field.
- Realistic demo PII, demo data in an explicitly empty app, or silent automation
  of destructive operations.

## Example — car rental business

User: "I need an app to manage my car rental business. I need to keep track of my
cars, their registration, who rents them, whether they are rented, and reminders
for service or document renewal."

Ground, then propose:
- **Cars** / Car: registration, make/model, availability (available, rented,
  maintenance), next_service_date, registration_expiry, insurance_expiry.
- **Renters** / Renter: contact details, with no invented real customer data.
- **Rentals** / Rental: car and renter lookups, start_date, due_date,
  returned_date, status (reserved, active, returned, cancelled).
- Fleet table and availability board; renter table; rental table/calendar.
- "Rent out a car" and "Return a car" app skills read the car and active
  rentals, then stage the Rental + Car status changes together. Explain that
  the procedure checks current availability but is not a concurrent booking lock.
- Daily read-only service/document check with an agreed lead window and local
  time, reporting here in chat. Ask for timezone if unknown.
- Demo Car A (available), Demo Car B (rented), Demo Renter A/B, then Demo Rental
  A (active, links Car B + Renter A), Demo Rental B (returned).

On confirmation build in dependency order: app → all three shaped tracks →
views → cars and renters → linked rentals → operating skills → reminder →
commit. No re-proposal. On approval verify the six requested outcomes against
actual data and the active routine. Provide the app link and how to rent out,
return, and update the next service/expiry dates.
