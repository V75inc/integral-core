# Agent action and approval matrix

**Status:** implementation inventory, 2026-10-06. This matrix reflects the Integral Core tool manifest and the current staged-write effect classifier; it is a code-derived baseline for policy review, not a claim that every external/system path has been browser-qualified.

## Enforcement rules

- `read`: ordinary authorization and tenant checks; no staged-write approval.
- `propose`: must use the declared staged-write path when implemented. User decision is scoped to one proposal.
- `execute`: executes only through its separate explicit policy gate; this classification does not imply chat approval.
- Effect classes apply to staged writes: `private_reversible`, `material_external`, and `destructive_security`. Unknown staged kinds default to `material_external`. Nested batches inherit their highest effect class.
- Durable `WorkApproval` and resource `Policy Approval` remain separate mechanisms with their own authority and lifecycle.

## Tool inventory

| Tool | Manifest class | Staging kind | Effect class | Policy action | Status |
|---|---|---|---|---|---|
| `integral_whoami` | `read` | `—` | `—` | `user.read` | `existing` |
| `integral_get_scope` | `read` | `—` | `—` | `—` | `existing` |
| `integral_get_page_context` | `read` | `—` | `—` | `—` | `existing` |
| `integral_list_workspaces` | `read` | `—` | `—` | `—` | `existing` |
| `integral_list_apps` | `read` | `—` | `—` | `app.read` | `existing` |
| `integral_get_app` | `read` | `—` | `—` | `app.read` | `existing` |
| `integral_invoke_app_operation` | `execute` | `—` | `—` | `app.read` | `existing` |
| `integral_list_tracks` | `read` | `—` | `—` | `track.read` | `existing` |
| `integral_get_track_schema` | `read` | `—` | `—` | `track.read` | `existing` |
| `integral_describe_substrate` | `read` | `—` | `—` | `—` | `existing` |
| `integral_check_design_coverage` | `read` | `—` | `—` | `—` | `existing` |
| `integral_verify_build` | `read` | `—` | `—` | `—` | `existing` |
| `integral_describe_model` | `read` | `—` | `—` | `track.read` | `existing` |
| `integral_describe_capabilities` | `read` | `—` | `—` | `—` | `existing` |
| `integral_governed_query` | `read` | `—` | `—` | `—` | `existing` |
| `integral_list_models` | `read` | `—` | `—` | `—` | `existing` |
| `integral_query_spec` | `read` | `—` | `—` | `—` | `existing` |
| `integral_query` | `read` | `—` | `—` | `entry.read` | `existing` |
| `integral_plan_query` | `read` | `—` | `—` | `entry.read` | `existing` |
| `integral_query_entries` | `read` | `—` | `—` | `entry.read` | `existing` |
| `integral_resolve_entry` | `read` | `—` | `—` | `entry.read` | `existing` |
| `integral_get_related` | `read` | `—` | `—` | `entry.read` | `existing` |
| `integral_search_cross_track` | `read` | `—` | `—` | `entry.read` | `existing` |
| `integral_get_digest` | `read` | `—` | `—` | `event_feed.subscribe` | `existing` |
| `integral_get_feed` | `read` | `—` | `—` | `event_feed.subscribe` | `existing` |
| `integral_count_entries` | `read` | `—` | `—` | `entry.read` | `existing` |
| `integral_aggregate` | `read` | `—` | `—` | `entry.read` | `existing` |
| `integral_activity_digest` | `read` | `—` | `—` | `entry.read` | `existing` |
| `integral_create_entry` | `propose` | `create_entry` | `private_reversible` | `entry.create` | `existing` |
| `integral_rank_destinations` | `read` | `—` | `—` | `—` | `existing` |
| `integral_file_content` | `propose` | `file_content` | `private_reversible` | `entry.create` | `existing` |
| `integral_update_entry` | `propose` | `update_entry` | `private_reversible` | `entry.update` | `existing` |
| `integral_delete_entry` | `propose` | `delete_entry` | `destructive_security` | `entry.delete` | `existing` |
| `integral_set_focus` | `propose` | `—` | `—` | `track.read` | `existing` |
| `integral_propose_design` | `propose` | `—` | `—` | `—` | `existing` |
| `integral_upsert_artifact` | `propose` | `—` | `—` | `—` | `existing` |
| `integral_get_artifact` | `propose` | `—` | `—` | `—` | `existing` |
| `integral_list_artifacts` | `propose` | `—` | `—` | `—` | `existing` |
| `integral_ask_user` | `propose` | `—` | `—` | `—` | `existing` |
| `integral_begin_batch` | `propose` | `—` | `—` | `—` | `existing` |
| `integral_build_approved_design` | `propose` | `—` | `—` | `—` | `existing` |
| `integral_commit_batch` | `propose` | `—` | `—` | `—` | `existing` |
| `integral_cancel_batch` | `propose` | `—` | `—` | `—` | `existing` |
| `integral_bulk_update_entries` | `propose` | `bulk_update_entries` | `material_external` | `entry.update` | `existing` |
| `integral_bulk_move_entries` | `propose` | `bulk_move_entries` | `material_external` | `entry.update` | `existing` |
| `integral_bulk_delete_entries` | `propose` | `bulk_delete_entries` | `destructive_security` | `entry.delete` | `existing` |
| `integral_link_entries` | `propose` | `link_entries` | `private_reversible` | `entry.update` | `existing` |
| `integral_transform_entry` | `propose` | `transform_entry` | `private_reversible` | `entry.create` | `existing` |
| `integral_add_entry_tag` | `propose` | `add_entry_tag` | `private_reversible` | `tag.assign` | `existing` |
| `integral_remove_entry_tag` | `propose` | `remove_entry_tag` | `private_reversible` | `tag.assign` | `existing` |
| `integral_list_tags` | `read` | `—` | `—` | `track.read` | `existing` |
| `integral_create_tag` | `propose` | `create_tag` | `private_reversible` | `tag.create` | `existing` |
| `integral_update_tag` | `propose` | `update_tag` | `private_reversible` | `tag.update` | `existing` |
| `integral_merge_tags` | `propose` | `merge_tags` | `material_external` | `tag.update` | `existing` |
| `integral_merge_tracks` | `propose` | `merge_tracks` | `material_external` | `track.delete` | `existing` |
| `integral_split_track` | `propose` | `split_track` | `material_external` | `track.update` | `existing` |
| `integral_register_track_template` | `propose` | `register_track_template` | `private_reversible` | `app.update` | `existing` |
| `integral_list_workspace_tools` | `read` | `—` | `—` | `tool.invoke` | `existing` |
| `integral_call_workspace_tool` | `propose` | `call_workspace_tool` | `material_external` | `tool.invoke` | `existing` |
| `integral_add_comment` | `propose` | `add_comment` | `private_reversible` | `comment.create` | `existing` |
| `integral_list_comments` | `read` | `—` | `—` | `entry.read` | `existing` |
| `integral_edit_comment` | `propose` | `edit_comment` | `private_reversible` | `comment.update` | `existing` |
| `integral_delete_comment` | `propose` | `delete_comment` | `destructive_security` | `comment.delete` | `existing` |
| `integral_get_model_draft` | `read` | `—` | `—` | `operational_model.author` | `existing` |
| `integral_propose_model_revision` | `propose` | `propose_profile_revision` | `private_reversible` | `operational_model.author` | `existing` |
| `integral_diff_model_draft` | `read` | `—` | `—` | `operational_model.author` | `existing` |
| `integral_publish_model_draft` | `propose` | `publish_profile_draft` | `destructive_security` | `migration.publish` | `existing` |
| `integral_discard_model_draft` | `propose` | `discard_profile_draft` | `private_reversible` | `operational_model.author` | `existing` |
| `integral_draft_new_model` | `propose` | `draft_new_profile` | `material_external` | `operational_model.author` | `existing` |
| `integral_apply_model_to_track` | `propose` | `apply_library_operational_model` | `destructive_security` | `operational_model.author` | `existing` |
| `integral_recommend_customizations` | `read` | `—` | `—` | `operational_model.author` | `existing` |
| `integral_author_model` | `propose` | `author_operational_model` | `material_external` | `operational_model.author` | `existing` |
| `integral_modify_model` | `propose` | `modify_operational_model.*` | `material_external` | `operational_model.author` | `existing` |
| `integral_list_views` | `read` | `—` | `—` | `view.read` | `existing` |
| `integral_save_view` | `propose` | `save_view` | `private_reversible` | `view.update` | `existing` |
| `integral_delete_view` | `propose` | `delete_view` | `destructive_security` | `view.delete` | `existing` |
| `integral_describe_dashboard_substrate` | `read` | `—` | `—` | `app.read` | `existing` |
| `integral_list_dashboards` | `read` | `—` | `—` | `app.read` | `existing` |
| `integral_suggest_dashboard` | `read` | `—` | `—` | `app.read` | `existing` |
| `integral_create_dashboard` | `propose` | `create_dashboard` | `private_reversible` | `app.update` | `existing` |
| `integral_update_dashboard` | `propose` | `update_dashboard` | `private_reversible` | `app.update` | `existing` |
| `integral_delete_dashboard` | `propose` | `delete_dashboard` | `destructive_security` | `app.update` | `existing` |
| `integral_create_track` | `propose` | `create_track` | `private_reversible` | `track.create` | `existing` |
| `integral_create_app` | `propose` | `create_app` | `private_reversible` | `app.create` | `existing` |
| `integral_create_app_track` | `propose` | `create_track` | `private_reversible` | `track.create` | `existing` |
| `integral_update_track` | `propose` | `update_track` | `private_reversible` | `track.update` | `existing` |
| `integral_delete_track` | `propose` | `delete_track` | `destructive_security` | `track.delete` | `existing` |
| `integral_update_app` | `propose` | `update_app` | `private_reversible` | `app.update` | `existing` |
| `integral_delete_app` | `propose` | `delete_app` | `destructive_security` | `app.delete` | `existing` |
| `integral_workspace_setup` | `propose` | `—` | `—` | `app.update` | `gap` |
| `integral_onboard_user` | `propose` | `—` | `—` | `user.create` | `gap` |
| `integral_get_access` | `read` | `—` | `—` | `app.read` | `existing` |
| `integral_share` | `propose` | `share` | `material_external` | `{resource_type}.share_link.mint` | `existing` |
| `integral_add_collaborator` | `propose` | `share` | `material_external` | `{resource_type}.collaborator_add` | `existing` |
| `integral_remove_collaborator` | `propose` | `remove_collaborator` | `destructive_security` | `{resource_type}.collaborator_remove` | `existing` |
| `integral_set_exclusion` | `propose` | `set_exclusion` | `destructive_security` | `{resource_type}.exclusion_add` | `existing` |
| `integral_remove_exclusion` | `propose` | `remove_exclusion` | `private_reversible` | `{resource_type}.exclusion_remove` | `existing` |
| `integral_mint_share_link` | `propose` | `mint_share_link` | `material_external` | `{resource_type}.share_link.mint` | `existing` |
| `integral_list_share_links` | `read` | `—` | `—` | `{resource_type}.read` | `existing` |
| `integral_revoke_share_link` | `propose` | `revoke_share_link` | `destructive_security` | `{resource_type}.share_link.revoke` | `existing` |
| `integral_invite` | `propose` | `invite` | `material_external` | `{target_type}.invitation_create` | `existing` |
| `integral_list_attachments` | `read` | `—` | `—` | `entry.read` | `existing` |
| `integral_list_track_attachments` | `read` | `—` | `—` | `track.read` | `existing` |
| `integral_list_workspace_attachments` | `read` | `—` | `—` | `workspace.read` | `existing` |
| `integral_get_attachment_text` | `read` | `—` | `—` | `entry.read` | `existing` |
| `integral_transcribe_audio` | `read` | `—` | `—` | `entry.read` | `existing` |
| `integral_attach_file` | `propose` | `attach_file` | `private_reversible` | `attachment.create` | `existing` |
| `integral_attach_uploaded_file_to_entry` | `propose` | `attach_uploaded_file` | `private_reversible` | `attachment.create` | `existing` |
| `integral_attach_uploaded_image_to_entry` | `propose` | `attach_uploaded_image` | `private_reversible` | `attachment.create` | `existing` |
| `integral_list_notifications` | `read` | `—` | `—` | `notification.read` | `existing` |
| `integral_mark_notification_read` | `execute` | `—` | `—` | `notification.read` | `existing` |
| `integral_query_audit_log` | `read` | `—` | `—` | `audit_log.read` | `existing` |
| `integral_list_conflicts` | `read` | `—` | `—` | `connector.read` | `existing` |
| `integral_resolve_conflict` | `propose` | `resolve_conflict` | `material_external` | `conflict.resolve` | `existing` |
| `integral_trigger_sync` | `propose` | `trigger_sync` | `material_external` | `connector.sync` | `existing` |
| `integral_export_view` | `read` | `—` | `—` | `entry.read` | `existing` |
| `integral_schedule_task` | `propose` | `routine_task_create` | `material_external` | `routine_task.create` | `existing` |
| `integral_list_routines` | `read` | `—` | `—` | `routine_task.read` | `existing` |
| `integral_update_routine` | `propose` | `routine_task_update` | `material_external` | `routine_task.update` | `existing` |
| `integral_cancel_routine` | `propose` | `routine_task_cancel` | `material_external` | `routine_task.delete` | `existing` |
| `integral_delete_routine` | `propose` | `routine_task_purge` | `destructive_security` | `routine_task.delete` | `existing` |
| `integral_author_skill` | `propose` | `author_skill` | `material_external` | `—` | `existing` |
| `integral_update_skill` | `propose` | `update_skill` | `private_reversible` | `—` | `existing` |
| `integral_delete_skill` | `propose` | `delete_skill` | `destructive_security` | `—` | `existing` |

## Review findings

- A `propose` tool with no staging kind is not classifiable by staged-write policy alone. The manifest currently marks these as design/context/deferred paths or gaps; their actual write effects need separate coverage in the capability and policy inventory.
- `integral_invoke_app_operation` and `integral_mark_notification_read` are manifest `execute` entries; the dispatch/policy gate remains authoritative for their concrete operations. Confirm their action semantics when the manifest is next reconciled.
- `integral_workspace_setup` and `integral_onboard_user` are manifest gaps. They are not covered by the staged-write classifier until implemented.
- The effect classifier is intentionally conservative for unrecognized kinds. Add a kind only alongside a reviewed policy classification and tests.

## Regeneration

Run `cd backend && .venv/bin/python` with `load_manifest()` and `effect_class()` from `app.agentive.tooling`; update this matrix whenever the tool manifest or approval policy changes.
