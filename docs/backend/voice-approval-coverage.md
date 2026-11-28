# Channel-neutral approval review coverage

Status: working migration plan. The common approval decision service remains the
authority. This is not a claim of complete verbal approval coverage.

## Invariant

A decision is about one canonical proposal, not about its presentation. Its
principal, workspace, conversation, work/run, target, binding, payload,
revision, expiry, authority policy, durable receipt, and executor are identical
regardless of whether it is presented through chat or a live call.

- No live call: offer the normal visual/chat review.
- Live call: offer the exact current review verbally at a quiet boundary when
  it can be safely understood and independently confirmed; show compact status
  instead of an automatically expanded approval card.
- When an effect cannot be fully conveyed or the required independent
  confirmation is unavailable, keep it pending and explain why; offer explicit
  visual inspection, never a weaker or model-generated decision.
- Reject ambiguous consent, stale/replaced proposals, insufficient authority,
  missing exact effect details, interrupted/incomplete playback, or expired
  capture. Never grant new permissions merely because a voice model spoke yes.
- Strong/destructive effects require a deliberate phrase and exact material
  consequence disclosure where a spoken equivalent is independently qualified.
  Existing stronger visual-only policy remains in force until that equivalent
  exists. External delivery, financial effects, permissions and unattended
  writes retain applicable independent approval and scope constraints.

## Shared descriptor target

Use one versioned descriptor from the immutable reviewed proposal with these
typed parts:

1. Decision family: staged write, policy approval, work mandate, design
   approval, or non-authorizing clarification question.
2. Principal/workspace/conversation and work/run binding, exact proposal and
   capability identity, revision and schema/declaration fingerprint, expiry.
3. Effect class and actual impact: recipients, destination, resources, scope
   change, reversibility, affected count, material consequence, future write
   scope and schedule where applicable.
4. Ordered material facts with stable field IDs, typed before/after/defaults,
   labels, units and disambiguators resolved under current authorization.
5. Decision options and required confirmation quality, digest of canonical
   payload and descriptor, permitted channel with explicit reason.
6. Family-specific executor and authoritative readback strategy.

Visual and verbal renderers should consume the same descriptor. A voice
projection must preserve every material fact; incomplete or unbounded facts
cannot be omitted from the decision to make the readout short. Bind the
independent caller confirmation to the exact freshly checked digest and active
call scope, use the canonical executor, verify the persisted result before
claiming success. The model can explain or request review, never establish
authority or decide channel eligibility.

## Current inventory

61 unique declared staging kinds, with a further batch-control alias. The
present implementation has seven kinds with at least one qualified voice
projection, frequently only a bounded subset. This is a coverage gap, not a
permission gap to bypass.

| Review group | Kinds | Next qualification |
|---|---|---|
| Existing bounded verbal projection | create_entry, update_entry, delete_entry, batch, routine_task_create, update_app, update_dashboard | Exact payload, revision/origin, active call and no-call parity. Batch currently limited to a record and one-time reminder; dashboard update currently metadata only. |
| Ordinary operations | create_app, create_track, update_track, save_view, create_dashboard, delete_view, delete_dashboard, add_entry_tag, remove_entry_tag, create_tag, update_tag, link_entries, transform_entry, add_comment, edit_comment, delete_comment, routine_task_update, routine_task_cancel | Full typed facts and authorized labels; deliberate stronger confirmation for destructive effects. Prioritize demo creation/layout and scheduling. |
| Rich or domain-specific operations | invoke_app_operation, call_workspace_tool, bulk_move_entries, bulk_delete_entries, file_content, attach_file, attach_uploaded_file, attach_uploaded_image, merge_tags, merge_tracks, split_track, configure_search_index, reindex_track, register_track_template, propose_profile_revision, discard_profile_draft, draft_new_profile, author_operational_model, modify_operational_model.*, resolve_conflict, trigger_sync, routine_task_purge, delete_track, delete_app, author_skill, update_skill, delete_skill, publish_profile_draft, apply_library_operational_model | Domain-specific effect facts, artifacts, bounds, stronger gate, explicit visual fallback if exact. Use declared App Operation inputs and declaration/target fingerprints. |
| Access and security | share, remove_collaborator, set_exclusion, remove_exclusion, mint_share_link, revoke_share_link, invite | Identity, audience, role, scope, expiry, exposure and propagation; preserve strong policy until equivalent approved, exact projection is independently qualified. |

Non-staged controls are distinct: focus/navigation, artifact preparation,
batch lifecycle and similar non-authorizing actions must not invent approval
requirements. Clarifying option questions are not authority. The saved
design/build approval path and work-policy/mandate approvals have their own
canonical revision, scope and executor binding and require descriptor adapters,
not a generic staged-token shortcut.

## Qualification gate

For each admitted kind and variant verify chat-only card compatibility; live
voice exact review, independent affirmative and negative capture, no effects
before consent; identity/scope/revision/expiry freshness; stale, interrupted,
ambiguous or replayed decisions denied; no hidden permission expansion; and
verified persisted readback. Ensure no automatic card expansion during a call,
and normal access to the original review once the call ends. Manifest drift
must not silently admit new operations. Test actual provider and browser audio
separately from simulated transport, with synthetic fixtures only.
