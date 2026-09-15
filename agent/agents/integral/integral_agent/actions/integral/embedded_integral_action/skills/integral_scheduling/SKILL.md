---


name: integral_scheduling
description: "Creates, lists, pauses, resumes, edits, and cancels routines — standing or one-shot instructions replayed as agent turns in the same chat thread. Use when the user asks for scheduled, deferred, or repeating agent work."
spec: jv
allowed-tools:
  - integral_schedule_task
  - integral_list_routines
  - integral_update_routine
  - integral_cancel_routine
  # Named to ground write_scope on a real track/entry before staging a routine.
  - integral_resolve_entry
  - integral_query_entries
  - integral_get_digest
  - integral_activity_digest
requires-actions:
  - EmbeddedIntegralAction
extends: action:integral/embedded_integral_action
tags:
  - integral
  - scheduling
  - routines
  - proactive

---

# Integral scheduling — SOP

A **routine** is an instruction that fires later (or on a cadence) and
replays as a normal agent turn, posting its reply into the SAME chat thread
it was created from. There is no separate "scheduled task" surface — a
routine is just this conversation, run again automatically.

**Never use `queue_task`.** It is disabled in this harness. Reminders and
schedules go through `integral_schedule_task` only — that is what shows in
Inbox / Background Tasks and what the scheduler actually runs.

## When to use this

- "Every day at 9am, prepare a morning briefing and leave it in chat."
- "Every morning around 6am, check the latest AI news and update the Latest
  in AI wiki."
- "Remind me every Monday what's overdue."
- "Remind me in two minutes to join my standup." / "Nudge me at 3pm."
- "Show my routines" / "what's scheduled" / "pause the morning briefing."

## When NOT to use this

- A one-off ask the user wants **answered now** ("summarize today's
  activity") → just answer it now; do not schedule.
- The instruction itself (digests, entry lookups, updates) → the existing
  tools for that job (`integral_activity_digest`, `integral_get_digest`,
  `integral_query_entries`, skill `integral_entries`, skill `integral_filing`, ...). This
  skill only owns the CADENCE / fire-time wrapper around whatever instruction the user
  gives you — it does not replace those tools.

## Grounding (read before write)

Before creating or updating a routine:

1. `integral_list_routines` — list existing routines so you do not duplicate
   cadence or guess `routine_id`.
2. When the instruction implies writes, `integral_resolve_entry` /
   `integral_query_entries` to obtain real `write_scope` resource ids.

## Creating a routine

1. **Pick recurring vs one-shot.**
   - **Recurring** ("every day at 9am", "every Monday") → compute a 5-field
     `cron` + IANA `timezone`. The tool does **not** parse natural language.
     If you don't know the user's timezone, ask once; do not guess.
   - **Deferred one-shot** ("in two minutes", "at 3pm today") → compute an
     absolute ISO-8601 `run_at` and **omit `cron`**. Defaults `max_runs=1`.
     Do not invent a cron that fires once.
2. **Run-count is a real dimension — use `max_runs`, never instruction text.**
   If the user asks for a fixed number of recurring occurrences ("every minute for 5
   iterations", "post this 10 times then stop"), extract that count and pass
   it as `max_runs` — the routine auto-transitions to `status: "completed"`
   after that many SUCCESSFUL runs (failed attempts don't count). If they
   don't mention a count on a recurring routine, omit `max_runs` entirely
   (open-ended). For a tiny burst that doesn't need to persist at all
   (e.g. "post 3 messages right now"), just do it directly.
3. **Write a self-contained `instruction` — and make it inert.** It is
   replayed VERBATIM on every run with no memory of this conversation, AS A
   NEW MESSAGE the agent receives with no signal that it's a scheduled
   replay rather than a fresh live ask. Write it as a complete standalone
   *task description*, not phrasing that could itself read as a request to
   configure something ("Remind the user it's time to join their standup" —
   good; "Remind me in two minutes to join my standup" — bad, still sounds
   like a scheduling ask on replay). Strip ALL cadence/meta language
   (frequency, "in N minutes", "then stop", "set up a routine for...") out of
   the stored `instruction` — cadence lives in `cron`/`timezone` or `run_at`,
   the stop condition lives in `max_runs`.
4. **If the instruction implies a WRITE** (e.g. "update the wiki", "add a
   row every day"), resolve the exact target first —
   `integral_resolve_entry` / `integral_query_entries` — and pass it as
   `write_scope: [{resource_type: "entry"|"track", resource_id}]`. This is
   the ONLY set of resources the routine may auto-apply writes to unattended
   on future runs; everything else it ever attempts always lands as a
   normal staged card for manual review, every run, forever. Never invent a
   `write_scope` id — only ids a tool actually returned this turn.
5. **Read-only routines** (briefings, digests, reminders) omit
   `write_scope` entirely — there is nothing to pre-approve.
6. Calling `integral_schedule_task` stages a card. Nothing runs until the user
   blesses it — say so plainly ("I'll set this up once you approve — it'll
   nudge you at 9:01 AM in this chat"). For short `run_at` windows, stress
   that they need to approve before the fire time.

## Executing a scheduled replay — do the work, don't reconfigure yourself

When a message you receive is a routine's replayed `instruction` (a standing
task description with no live back-and-forth, arriving on its own — not a
fresh question a human just typed), **just do the work it describes and
reply with the result.** Never call `integral_schedule_task` /
`integral_update_routine` / `integral_cancel_routine` in response to a
replayed instruction, even if its wording sounds like a scheduling request
("every day at...", "for N iterations", "then stop") — that phrasing is
DESCRIBING what the already-existing routine does, not asking you to
(re)configure it. Re-invoking this skill on your own replay is how a routine
silently stops posting real check-ins and starts spawning duplicate
`AWAITING APPROVAL` cards instead. Only call these tools when a live user,
in this turn, is directly asking you to create/update/cancel a routine.

## Listing / managing

- `integral_list_routines` — the user's own routines with `next_run_at` /
  `last_run_status` / `write_scope`. Call this FIRST before any
  update/cancel — you need the real `routine_id`, never guess one.
- `integral_update_routine` — pause, resume, or edit cadence/instruction/
  write_scope/max_runs. **Widening `write_scope`** (adding a new auto-apply
  target) re-stages for a fresh bless — the user must explicitly re-approve;
  never claim an expanded grant took effect before they do. Resuming a
  `status: "completed"` routine (`status: "active"`) restarts its run count
  from 0 — say so, don't imply it picks up where it left off. Pass
  `clear_max_runs: true` to turn a capped routine open-ended again.
- `integral_cancel_routine` — stops it permanently. Confirm before calling
  if the user's intent was ambiguous ("pause" vs "cancel" are different —
  pausing is resumable, cancelling is not).

## Honesty rules

- Never claim a routine is active, will fire, or "already posted" until the
  staged card is blessed (`[SYSTEM:STAGING-RESOLVED]`) and/or
  `integral_list_routines` shows it. Staging alone is not enough.
- Never claim a routine "ran" or "already posted the briefing" — that's
  only true if `last_run_status == "success"` on `integral_list_routines`.
  If `last_run_status` is `"error"` or the routine is `status: "paused"`
  (auto-paused after repeated failures or a lost access grant), say so
  plainly and suggest the user check chat / re-grant access, don't paper
  over it. `status: "completed"` means it finished its `max_runs` budget
  naturally — that's a success, not a failure; distinguish it from
  `"cancelled"` (the user stopped it) when reporting status.
- Never claim a write was auto-applied unattended unless the target was
  explicitly in that routine's `write_scope` at creation time — anything
  outside the grant always surfaces as a normal staged card, same as a live
  chat turn.
- Delete-class operations (deleting an entry or track) are **never**
  auto-applied by a routine, no matter what `write_scope` says or what the
  user asks for in the schedule — they always require a manual bless on
  each occurrence. Don't promise otherwise.

## Staging discipline

`integral_schedule_task`, `integral_update_routine`, and widening
`write_scope` **stage** routine cards — nothing runs until the user
blesses. Say plainly what cadence / fire time and auto-apply grants you are proposing.
Wait for `[SYSTEM:STAGING-RESOLVED]` before claiming a routine is active.

## Scope

Personal routines only — bound to the creating user, posting into a thread
that user owns. There is no shared/workspace-wide routine surface yet; if
asked, say so rather than staging something that silently stays personal.

## Examples

> **User:** "Every weekday at 9am ET, post a morning briefing here."

1. `integral_list_routines` → check no duplicate morning-briefing routine.
2. Cadence: `cron="0 9 * * 1-5"`, `timezone="America/New_York"`.
3. Instruction (inert): `"Prepare a morning briefing summarizing recent
   activity across my accessible tracks and highlight anything that needs
   attention today."`
4. `integral_schedule_task(cron=..., timezone=..., instruction=...)` → stage.
5. Present: "I'll set this up once you approve — weekdays 9am ET, posts here."

> **User:** "Remind me in two minutes to join my standup."

1. Compute `run_at` = now + 2 minutes (ISO-8601 with offset).
2. Instruction (inert): `"Remind the user it's time to join their standup.
   Send a short, direct nudge."`
3. `integral_schedule_task(run_at=..., instruction=...)` — omit `cron`.
4. Present: "I'll nudge you then once you approve this reminder — please
   bless it before that time." Wait for bless before claiming it's set.
