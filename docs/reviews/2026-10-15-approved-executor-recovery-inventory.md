# Approved executor recovery inventory — 2026-10-15

Source snapshot after Core `07dc7750`, including the qualified standalone View deletion recovery increment.
The dispatcher and `CANONICAL_COMMAND_KINDS` are authoritative after this date.
This is command-integration coverage, not a test pass percentage or goal completion score.

69 registered kinds: 26 in the native command inventory, one separate canonical
App-operation integration, one conditional batch dispatcher, one retired
design-card tombstone that refuses effects, and 40 other effectful kinds
requiring effect/recovery integration. The 2026-10-16 design follow-through below
supersedes the historical design-executor classification. The App-operation path reuses its existing
canonical receipt rather than joining `CANONICAL_COMMAND_KINDS`; its local graph
qualification does not cover arbitrary external handler effects. Final artifact,
GitHub and wider system qualification remain distinct.

| Kind | Executor | Native staging receipt integration |
| --- | --- | --- |
| `add_comment` | `_x_add_comment` | Integrated on transactional graph stores |
| `add_entry_tag` | `_x_add_entry_tag` | Canonical delta/receipt integration; native, full backend, CI-faithful and artifact gates passed |
| `apply_library_operational_model` | `_x_apply_library_operational_model` | Not in native staging inventory; qualify underlying effects |
| `apply_to_draft` | `_x_propose_profile_revision` | Not in native staging inventory; qualify underlying effects |
| `attach_file` | `_x_attach_file` | Not in native staging inventory; qualify underlying effects |
| `attach_uploaded_file` | `_x_attach_uploaded_file` | Not in native staging inventory; qualify underlying effects |
| `attach_uploaded_image` | `_x_attach_uploaded_image` | Not in native staging inventory; qualify underlying effects |
| `author_operational_model` | `_x_author_operational_model` | Not in native staging inventory; qualify underlying effects |
| `author_skill` | `_x_author_skill` | Not in native staging inventory; qualify underlying effects |
| `batch` | `_x_batch` | Conditional: every child kind must be supported |
| `bulk_delete_entries` | `_x_bulk_delete_entries` | Not in native staging inventory; qualify underlying effects |
| `bulk_move_entries` | `_x_bulk_move_entries` | Not in native staging inventory; qualify underlying effects |
| `bulk_update_entries` | `_x_bulk_update_entries` | Not in native staging inventory; qualify underlying effects |
| `call_workspace_tool` | `_x_call_workspace_tool` | Not in native staging inventory; qualify underlying effects |
| `create_app` | `_x_create_app` | Integrated on transactional graph stores |
| `create_dashboard` | `_x_create_dashboard` | Integrated on transactional graph stores |
| `create_entry` | `_x_create_entry` | Integrated on transactional graph stores |
| `create_tag` | `_x_create_tag` | Not in native staging inventory; qualify underlying effects |
| `create_track` | `_x_create_track` | Integrated on transactional graph stores |
| `delete_app` | `_x_delete_app` | Integrated on transactional graph stores |
| `delete_comment` | `_x_delete_comment` | Integrated on transactional graph stores |
| `delete_dashboard` | `_x_delete_dashboard` | Integrated on transactional graph stores |
| `delete_entry` | `_x_delete_entry` | Integrated on transactional graph stores |
| `delete_skill` | `_x_delete_skill` | Not in native staging inventory; qualify underlying effects |
| `delete_track` | `_x_delete_track` | Integrated on transactional graph stores |
| `delete_view` | `_x_delete_view` | Integrated on PostgreSQL; local source gates passed |
| `design_proposal` | `_x_design_proposal` | Retired: refuses all effects; current native design selection retains its separate authority contract |
| `discard_profile_draft` | `_x_discard_profile_draft` | Not in native staging inventory; qualify underlying effects |
| `draft_new_profile` | `_x_draft_new_profile` | Not in native staging inventory; qualify underlying effects |
| `edit_comment` | `_x_edit_comment` | Integrated on transactional graph stores |
| `file_content` | `_x_file_content` | Not in native staging inventory; qualify underlying effects |
| `invite` | `_x_invite` | Not in native staging inventory; qualify underlying effects |
| `invoke_app_operation` | `_x_invoke_app_operation` | Separate canonical receipt integration on transactional graph stores; local graph contract only |
| `link_entries` | `_x_link_entries` | Not in native staging inventory; qualify underlying effects |
| `mcp_tool_call` | `_x_mcp_tool_call` | Not in native staging inventory; qualify underlying effects |
| `merge_tags` | `_x_merge_tags` | Not in native staging inventory; qualify underlying effects |
| `merge_tracks` | `_x_merge_tracks` | Not in native staging inventory; qualify underlying effects |
| `mint_share_link` | `_x_mint_share_link` | Not in native staging inventory; qualify underlying effects |
| `modify_operational_model.add_entry_type` | `_x_modify_operational_model` | Integrated on PostgreSQL; local source gates passed |
| `modify_operational_model.add_tag` | `_x_modify_operational_model` | Integrated on PostgreSQL; local source gates passed |
| `modify_operational_model.add_view` | `_x_modify_operational_model` | Integrated on PostgreSQL; local source gates passed |
| `modify_operational_model.remove_entry_type` | `_x_modify_operational_model` | Integrated on PostgreSQL; local source gates passed |
| `modify_operational_model.remove_tag` | `_x_modify_operational_model` | Integrated on PostgreSQL; local source gates passed |
| `modify_operational_model.remove_view` | `_x_modify_operational_model` | Integrated on PostgreSQL; local source gates passed |
| `modify_operational_model.update_view` | `_x_modify_operational_model` | Integrated on PostgreSQL; local source gates passed |
| `propose_profile_revision` | `_x_propose_profile_revision` | Not in native staging inventory; qualify underlying effects |
| `publish_profile_draft` | `_x_publish_profile_draft` | Not in native staging inventory; qualify underlying effects |
| `register_track_template` | `_x_register_track_template` | Not in native staging inventory; qualify underlying effects |
| `remove_collaborator` | `_x_remove_collaborator` | Not in native staging inventory; qualify underlying effects |
| `remove_entry_tag` | `_x_remove_entry_tag` | Canonical delta/receipt integration; native, full backend, CI-faithful and artifact gates passed |
| `remove_exclusion` | `_x_remove_exclusion` | Not in native staging inventory; qualify underlying effects |
| `resolve_conflict` | `_x_resolve_conflict` | Not in native staging inventory; qualify underlying effects |
| `revoke_share_link` | `_x_revoke_share_link` | Not in native staging inventory; qualify underlying effects |
| `routine_task_cancel` | `_x_routine_task_cancel` | Not in native staging inventory; qualify underlying effects |
| `routine_task_create` | `_x_routine_task_create` | Not in native staging inventory; qualify underlying effects |
| `routine_task_purge` | `_x_routine_task_purge` | Not in native staging inventory; qualify underlying effects |
| `routine_task_update` | `_x_routine_task_update` | Not in native staging inventory; qualify underlying effects |
| `save_view` | `_x_save_view` | Integrated on transactional graph stores |
| `set_exclusion` | `_x_set_exclusion` | Not in native staging inventory; qualify underlying effects |
| `share` | `_x_share` | Not in native staging inventory; qualify underlying effects |
| `split_track` | `_x_split_track` | Not in native staging inventory; qualify underlying effects |
| `transform_entry` | `_x_transform_entry` | Not in native staging inventory; qualify underlying effects |
| `trigger_sync` | `_x_trigger_sync` | Not in native staging inventory; qualify underlying effects |
| `update_app` | `_x_update_app` | Integrated on transactional graph stores |
| `update_dashboard` | `_x_update_dashboard` | Integrated on transactional graph stores |
| `update_entry` | `_x_update_entry` | Integrated on transactional graph stores |
| `update_skill` | `_x_update_skill` | Not in native staging inventory; qualify underlying effects |
| `update_tag` | `_x_update_tag` | Not in native staging inventory; qualify underlying effects |
| `update_track` | `_x_update_track` | Integrated on transactional graph stores |

## Required next boundaries

- Retain the current seven-command `modify_operational_model.*` and standalone `delete_view` source/artifact evidence while qualifying the remaining system and principal matrix. Standalone deletion now shares admission and transactional event handling across HTTP and staging; other View mutations still need their own API/native acceptance audit.
- Current model-created EntryType, View and Tag batch references rebuild from private receipts after later deletion. Preserve this contract when extending other families and finish the broader recovery matrix.
- Classify graph-only commands separately from file/object bytes, connector/MCP/tool I/O, invitations/notifications, operations and routines. A command absent from this inventory may already use another durable service; inspect it before adding a duplicate journal.
- External outcomes require durable intent, identity, idempotency/readback and reconciliation. An unknown network outcome must never become permission for blind replay.
- Bulk wrappers require stable per-child receipts and an honest partial outcome. A loop around an integrated child is not proof that the wrapper can recover without repeating it.
- Access-changing effects require the same membership/identity ordering as concurrent release and revocation, plus redaction of current results after access is removed.
- Profile/draft/publication mutations require their canonical model authority and complete affected-owner set, not just the scalar Track/App in the proposal.
- Design acceptance is not a completed App build; retain its explicit `needs_agent_build` outcome.

R1/R2 remain incomplete until each row has a justified and qualified recovery contract, including commands whose durable service differs from the native graph receipt path.

## Remaining family classification

The original source triage of 44 non-native kinds below is **not qualification**.
The separate App-operation local integration now has combined native evidence;
40 other effectful kinds remain, the retired design card refuses effects,
and arbitrary external App handlers still need explicit
dispatch/readback contracts. The
classification identifies the effect owner to integrate and prevents a second
journal being added around a service that already owns durable execution.
References are relative to `backend/app/`; the dispatcher remains
`agentive/staging_executors.py`.

| Kinds | Current effect boundary | Required recovery work |
| --- | --- | --- |
| `add_entry_tag`, `remove_entry_tag` | `api/entries.py` handles entry/tag relationships | Bind both canonical endpoints, idempotent edge identity, atomic event/receipt, and current-result readback; an existing edge alone is not an exact approved-command receipt. |
| `create_tag`, `update_tag` | `api/tags.py` handles model/Track taxonomy | Resolve actual model/owner authority, join one graph/event/receipt commit, recover current authorized taxonomy. |
| `link_entries` | Executor computes relation cardinality then calls Entry update | Freeze approved source/target/field semantics under admission; preserve unrelated concurrent relation values and exact command identity. Do not relabel it as a generic Entry update approval. |
| `file_content` | Executor creates an Entry then applies filing facets/tags and optional personalization | Make required filing facets part of the canonical effect, with private created identity; classify personalization as optional post-commit work. A created Entry alone does not prove successful filing. |
| `bulk_update_entries`, `bulk_delete_entries` | Executor loops Entry handlers with saved progress | Replace progress-as-proof with stable child command identities/receipts and parent decision validation. Loss after a committed child but before cursor persistence must not repeat the child. |
| `bulk_move_entries`, `merge_tracks`, `split_track`, `merge_tags` | `services/bulk_move_entries.py`, `track_restructuring.py`, `tag_merge.py` own compound graph changes and some transaction boundaries | Preserve those services' graph/schema/grant semantics; integrate receipts at their existing effect boundary. Qualify all involved resources, partial outcomes, events and child identities before advertising recoverability. |
| `author_operational_model`, `apply_library_operational_model`, `register_track_template`, `draft_new_profile`, `apply_to_draft`, `propose_profile_revision`, `publish_profile_draft`, `discard_profile_draft` | `services/operational_model_authoring.py`; two proposal kinds share a handler | Bind library/draft/publication lineage and complete affected-owner authority. Persist generated model/child identities and approval input; recover publication state without rematerializing children or reapplying a patch. |
| `author_skill`, `update_skill`, `delete_skill` | `services/agent_skill_authoring.py` | Inventory durable graph/catalog and any file/loader effects before selecting atomic or intent-based recovery. Retain current agent/workspace skill authority. |
| `share`, `remove_collaborator`, `set_exclusion`, `remove_exclusion` | Per-resource collaboration/exclusion routes, with membership/grant and notification effects | Order actor/recipient identity and resource/membership admission; commit grant/exclusion state and receipt together, move delivery to recoverable outboxes, and withhold results after revocation. |
| `mint_share_link`, `revoke_share_link`, `invite` | `services/share_links.py`, `api/shares.py`, invitation routes | Preserve hashed token identity and expiry/revocation limits. Store only protected command evidence, deduplicate token issuance/notification dispatch, and distinguish committed invitation/link state from delivery outcome. Never log or regenerate a secret token to simulate recovery. |
| `attach_file`, `attach_uploaded_image` | `services/attachment_agent.py` writes bytes plus graph/storage state | Durable byte intent, immutable content identity, storage accounting and orphan reconciliation are required; graph rollback alone cannot undo an unknown object-store outcome. |
| `attach_uploaded_file` | `services/attachment_agent.py` reparents existing validated attachment graph state; no new bytes | Join relationship/owner/cache changes and event/receipt atomically; retain source ownership and current target authorization. This needs a different effect contract from byte uploads. |
| `call_workspace_tool`, `mcp_tool_call`, `trigger_sync`, `transform_entry` | Workspace tool dispatcher, connector proxy/sync, or extension handoff hook | Inspect existing service idempotency; persist dispatch intent before network/tool execution, retain provider operation identity/readback, and reconcile unknown outcomes without blind replay. A returned error may follow partial external success. |
| `invoke_app_operation` | `services/app_operations/dispatch.py` owns the canonical transaction and receipt | Local decision/effect integration qualified by 224 combined native cases plus three separate expiry/revocation cases; see [atomicity report](2026-10-15-staged-app-operation-atomicity.md). Original complete review and declaration have independent private fingerprints. External effects and final system gates remain open. |
| `resolve_conflict` | `api/conflicts.py` resolves recorded conflict state | Inventory selected resolution's local/connector effects and immutable conflict revision before choosing graph receipt or external intent recovery. |
| `routine_task_create`, `routine_task_update`, `routine_task_cancel`, `routine_task_purge` | `agentive/services/routine_tasks.py`, with executor ChangeEvents | Command recovery must cover schedule record plus emitted event and exact routine identity. The existing scheduled-run WorkItem idempotency key protects a different boundary; it does not deduplicate routine creation. |
| `design_proposal` | Retired compatibility tombstone, with no graph write or thread lookup | Old cards return `design_approval_reconciliation_required`. Current native design approval validates exact revision, latest user turn, session edge/run and current identity/workspace admission. It does not claim the later App build completed. Native historical evidence and the broader recovery matrix remain separate requirements. |

These groups enumerate the original 44 non-native kind names; aliases count as
registered kinds. After an integration, update the table and native inventory
from the actual dispatcher and qualify its denied, unavailable, interrupted,
post-commit, and cross-worker paths. No family is closed by this classification.


## 2026-10-16 design approval follow-through

The current `integral_propose_design` dispatcher records an outline and artifact;
it does not create a staged design card. The obsolete executor previously stamped
the current marker from an old payload and could replace its summary/proposal.
It now refuses effects with an explicit reconciliation error. Persisted cards
are retained; none is consumed, migrated or interpreted as fresh approval.
The native design capability remains available through its existing primary
model selection, exact revision and current-run CAS service.

The native service's early return for an already-approved marker also bypassed
its run fence. The PostgreSQL regression reproduced two failures for prior
approvals: stale runs returned success, and current runs failed to bind the new
approval to themselves. That shortcut is removed. The write now joins current
identity/workspace admission, requires one owning session edge, validates the
active session/run and uses its existing revision CAS. Same-run repeated
selection preserves the approval timestamp. Native design approval is still
separate from staged build effects and their receipts; no native historical
approval-journal or whole-goal completion is claimed by this increment.

## 2026-10-16 Entry-tag delta integration

Both tag delta kinds now compose the canonical Entry revision/validation boundary
and their own exact command receipts, rather than relabeling their approval as
`update_entry`. Their native component tests cover effect/receipt rollback, lost
completion, current readback after a later edit, canonical taxonomy containment,
revoked authority, two-pool different-delta concurrency and separate-interpreter
same-approval attempts. The combined source passed its full backend,
CI-faithful, native and independent artifact gates. See [the qualification report](2026-10-16-entry-tag-command-recovery.md).
The historical classification table above identifies the original boundaries;
it is not a statement that these two integrations are absent on this source.
