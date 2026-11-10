---
name: integral-goals
description: 'Set, advance, review, or close a standing workspace goal with an owner, measurable outcome, and evidence. Use for requests to track an outcome across multiple tasks, assess whether a milestone meets its criteria, or revisit a blocked or recurring goal. Use integral-entries for a single record change and integral-scheduling for cadence.'
allowed-tools: integral_find_workspace_resources integral_list_tracks integral_get_track_schema integral_list_workspace_tools integral_call_workspace_tool integral_query integral_query_entries integral_resolve_entry integral_create_entry integral_file_content integral_link_entries
---

# Integral goals

## Set the goal

- Identify the outcome, accountable owner, evidence that would count as
  success, and any required reviewer. Capture the deadline, priority, scope,
  and constraints when known. Ask about missing information when it affects
  whether the goal can be accepted or measured. Record unresolved details as
  such. Do not invent an owner or assume the resident owns the outcome.
- Search the active workspace for an existing goal, project, or outcome
  record before proposing a new one. Inspect the Track's schema and honor
  any registered App read or command. A search hit is a candidate. Verify
  the exact record and its existing criteria.
- If a standing record is useful and a compatible Track exists, stage a
  new Entry in that Track using its published schema. When the existing
  schema cannot express the requested goal, use the appropriate schema
  skill to propose the needed structure within the relevant App. Preserve
  the agreed outcome through approval and link to the saved Entry.

## Advance the work

- Break the outcome into observable milestones and next actions. Reuse
  existing tasks. Link records through declared relation fields, or cite
  the Entry links in the goal when the schema has no relation. Separate
  verified progress from expected progress and name blockers and dependencies.
- A goal describes the outcome. An assignment asks for specific work, a
  mandate limits authorized action, and a routine determines when to
  reassess. Use the existing review path for each effect. An approved goal
  supplies no authority to apply a change or spend resources. A change in
  scope, success criteria, or authority requires a fresh review.
- For a requested recurring check, use the scheduling skill and bind the
  routine to the exact goal. For a typed goal use task_watch with watch_goal
  true and the goal Entry ID. Default the check to
  reading and reporting. Keep the standing goal visible when blocked or
  paused, and identify what would unblock it.

## Review or close

- Re-read the goal and each material evidence source. For every criterion,
  say what the evidence establishes and what remains unverified. A completed
  task or passed deadline cannot substitute for outcome evidence. If evidence
  or required reviewer confirmation is missing, report the gap and leave the
  goal open. For a recurring goal, evaluate the current cycle separately.
- Stage a progress, scope, or completion update only after reading the
  current Entry and Track schema. Use the App's declared command if required.
  Otherwise use `integral_file_content` in update mode with the current Entry
  record revision and live Track schema revision. For closure, pass each
  supporting Entry ID and its record revision in `evidence_revisions` so
  approval checks the same evidence. For an EntryType with published
  goal semantics, use append mode with append_field set to body and
  close_goal set to true. You can omit evidence_revisions for a typed goal
  so Core reads the declared evidence and pins its current revisions when
  every criterion passes. Use this when a goal watch suggests closure and
  the user asks you to prepare the review. Core checks the criteria again on
  approval and saves the completed
  status with the closure note. Use the declared evidence relations. If the current revisions are missing or
  the evidence cannot be bound, leave closure pending. Preserve unrelated content.
  Re-read before drafting a replacement for a stale proposal.
- Confirm completion only when the approved update has applied and a readback
  shows the expected state. Link to the goal, the decisive evidence, and any
  remaining follow-up.
