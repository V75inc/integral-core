# API source reference

This inventory is extracted from literal @endpoint declarations in backend API modules. It does not establish that every declaration is mounted in every configuration, and it excludes dynamic declarations and non-endpoint transports. Authentication, input/output schemas, prefixes, policy, and deployment availability are determined by the actual runtime and handler contract.

Open the deployed backend's `/docs` or `/openapi.json` for its resolved API. Ordinary source scope uses `X-Integral-Scope: ws:<workspace_id>`. Never infer write authority from a route appearing here.

For App authors, prefer the [public facade](../platform/extension-contract-v1.md) rather than coupling a package to private handler functions. [Chat](ai-chat.md), [connectors](connectors.md), [model lifecycle](../operational-models/DRAFT_PUBLISH.md), and [service writes](service-layer.md) explain behavior.

Regenerate with `backend/.venv/bin/python scripts/build_documentation.py --inventory-only`. The [JSON inventory](../generated/api-routes.json) preserves source/handler identity for tooling.

## agent_skills

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/agentive/skills` | [list_skills](../../backend/app/agentive/api/agent_skills.py) |
| POST | `/agentive/skills` | [create_skill](../../backend/app/agentive/api/agent_skills.py) |
| GET | `/agentive/skills/effective` | [effective_skill_context](../../backend/app/agentive/api/agent_skills.py) |
| GET | `/agentive/skills/tool-catalogue` | [tool_catalogue](../../backend/app/agentive/api/agent_skills.py) |
| DELETE | `/agentive/skills/{skill_id}` | [delete_skill](../../backend/app/agentive/api/agent_skills.py) |
| GET | `/agentive/skills/{skill_id}` | [get_skill](../../backend/app/agentive/api/agent_skills.py) |
| PATCH | `/agentive/skills/{skill_id}` | [patch_skill](../../backend/app/agentive/api/agent_skills.py) |
| POST | `/agentive/skills/{skill_id}/reset` | [reset_skill](../../backend/app/agentive/api/agent_skills.py) |

## agent_tools

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/agentive/tools` | [list_tools](../../backend/app/agentive/api/agent_tools.py) |
| POST | `/agentive/tools/{tool_name}` | [execute_tool_endpoint](../../backend/app/agentive/api/agent_tools.py) |

## channels

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/agentive/channels/identities` | [list_channel_identities](../../backend/app/agentive/api/channels.py) |
| POST | `/agentive/channels/identities` | [create_channel_identity](../../backend/app/agentive/api/channels.py) |
| POST | `/agentive/channels/identities/resolve` | [resolve_identity](../../backend/app/agentive/api/channels.py) |
| DELETE | `/agentive/channels/identities/{identity_id}` | [delete_channel_identity](../../backend/app/agentive/api/channels.py) |
| POST | `/agentive/channels/identities/{identity_id}/verify` | [verify_channel_identity](../../backend/app/agentive/api/channels.py) |
| POST | `/agentive/channels/identities/{identity_id}/verify-link-token` | [verify_link_token](../../backend/app/agentive/api/channels.py) |
| POST | `/agentive/channels/whatsapp/verify-initiate` | [whatsapp_verify_initiate](../../backend/app/agentive/api/channels.py) |
| POST | `/agentive/channels/whatsapp/verify-otp` | [whatsapp_verify_otp](../../backend/app/agentive/api/channels.py) |

## chat

| Methods | Declared path | Handler |
|---|---|---|
| POST | `/agentive/chat/message` | [post_agentive_chat_message](../../backend/app/agentive/api/chat.py) |

## connectors

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/agentive/connectors` | [list_connectors](../../backend/app/agentive/api/connectors.py) |
| POST | `/agentive/connectors` | [post_create_connector](../../backend/app/agentive/api/connectors.py) |
| GET | `/agentive/connectors/catalog` | [list_connector_catalog_endpoint](../../backend/app/agentive/api/connectors.py) |
| GET | `/agentive/connectors/catalog/{slug}` | [get_connector_catalog_entry_endpoint](../../backend/app/agentive/api/connectors.py) |
| POST | `/agentive/connectors/catalog/{slug}/install` | [install_connector_from_catalog_endpoint](../../backend/app/agentive/api/connectors.py) |
| POST | `/agentive/connectors/gmail/oauth/callback` | [post_gmail_oauth_callback](../../backend/app/agentive/api/connectors.py) |
| POST | `/agentive/connectors/gmail/oauth/start` | [post_gmail_oauth_start](../../backend/app/agentive/api/connectors.py) |
| POST | `/agentive/connectors/mcp/mount` | [mount_mcp_connector_endpoint](../../backend/app/agentive/api/connectors.py) |
| POST | `/agentive/connectors/mcp/registry/mount` | [mount_mcp_from_registry_endpoint](../../backend/app/agentive/api/connectors.py) |
| GET | `/agentive/connectors/mcp/registry/preview` | [preview_mcp_registry_mount_endpoint](../../backend/app/agentive/api/connectors.py) |
| GET | `/agentive/connectors/mcp/registry/search` | [search_mcp_registry_endpoint](../../backend/app/agentive/api/connectors.py) |
| GET | `/agentive/connectors/mcp/registry/servers` | [get_mcp_registry_server_endpoint](../../backend/app/agentive/api/connectors.py) |
| POST | `/agentive/connectors/quickbooks/authorize` | [post_quickbooks_authorize](../../backend/app/agentive/api/connectors.py) |
| POST | `/agentive/connectors/quickbooks/callback` | [post_quickbooks_callback](../../backend/app/agentive/api/connectors.py) |
| DELETE | `/agentive/connectors/{connector_id}` | [delete_connector_by_id](../../backend/app/agentive/api/connectors.py) |
| GET | `/agentive/connectors/{connector_id}` | [get_connector_by_id](../../backend/app/agentive/api/connectors.py) |
| PATCH | `/agentive/connectors/{connector_id}` | [patch_connector](../../backend/app/agentive/api/connectors.py) |
| GET | `/agentive/connectors/{connector_id}/bindings` | [list_bindings](../../backend/app/agentive/api/connectors.py) |
| POST | `/agentive/connectors/{connector_id}/bindings` | [post_create_binding](../../backend/app/agentive/api/connectors.py) |
| DELETE | `/agentive/connectors/{connector_id}/bindings/{track_id}` | [delete_binding](../../backend/app/agentive/api/connectors.py) |
| GET | `/agentive/connectors/{connector_id}/gmail/labels` | [get_gmail_labels](../../backend/app/agentive/api/connectors.py) |
| POST | `/agentive/connectors/{connector_id}/gmail/labels` | [post_gmail_set_labels](../../backend/app/agentive/api/connectors.py) |
| GET | `/agentive/connectors/{connector_id}/health` | [get_mcp_connector_health](../../backend/app/agentive/api/connectors.py) |
| POST | `/agentive/connectors/{connector_id}/mcp/reauthorize` | [reauthorize_mcp_connector_endpoint](../../backend/app/agentive/api/connectors.py) |
| POST | `/agentive/connectors/{connector_id}/mcp/refresh` | [refresh_mcp_connector_endpoint](../../backend/app/agentive/api/connectors.py) |
| GET | `/agentive/connectors/{connector_id}/tools` | [list_connector_tools](../../backend/app/agentive/api/connectors.py) |

## conversations

| Methods | Declared path | Handler |
|---|---|---|
| POST | `/agentive/conversations/context` | [create_conversation_context](../../backend/app/agentive/api/conversations.py) |
| DELETE | `/agentive/conversations/context/{context_id}` | [delete_conversation_context](../../backend/app/agentive/api/conversations.py) |
| GET | `/agentive/conversations/context/{context_id}` | [get_conversation_context](../../backend/app/agentive/api/conversations.py) |
| PATCH | `/agentive/conversations/context/{context_id}` | [patch_conversation_context](../../backend/app/agentive/api/conversations.py) |

## mcp_connectors

| Methods | Declared path | Handler |
|---|---|---|
| POST | `/agentive/connectors/mcp/oauth/callback` | [post_mcp_oauth_callback](../../backend/app/agentive/api/mcp_connectors.py) |

## proactive

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/agentive/proactive/digest` | [get_user_digest](../../backend/app/agentive/api/proactive.py) |
| POST | `/agentive/proactive/log-push` | [proactive_log_push](../../backend/app/agentive/api/proactive.py) |
| PATCH | `/agentive/proactive/preferences` | [update_proactive_preferences](../../backend/app/agentive/api/proactive.py) |
| POST | `/agentive/proactive/push` | [proactive_push_deprecated_redirect](../../backend/app/agentive/api/proactive.py) |
| GET | `/agentive/proactive/reminders` | [get_user_reminders](../../backend/app/agentive/api/proactive.py) |
| GET | `/agentive/proactive/users-needing-digest` | [get_users_needing_digest](../../backend/app/agentive/api/proactive.py) |

## prompt_queue

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/agentive/prompt-queue` | [get_prompt_queue_endpoint](../../backend/app/agentive/api/prompt_queue.py) |
| POST | `/agentive/prompt-queue/cancel-all` | [cancel_all_endpoint](../../backend/app/agentive/api/prompt_queue.py) |
| POST | `/agentive/prompt-queue/mark-write` | [mark_write_endpoint](../../backend/app/agentive/api/prompt_queue.py) |
| POST | `/agentive/prompt-queue/resolve-question` | [resolve_question_endpoint](../../backend/app/agentive/api/prompt_queue.py) |

## questions

| Methods | Declared path | Handler |
|---|---|---|
| POST | `/agentive/questions/answer` | [answer_question_endpoint](../../backend/app/agentive/api/questions.py) |
| GET | `/agentive/questions/pending` | [pending_question_endpoint](../../backend/app/agentive/api/questions.py) |

## routines

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/agentive/routines` | [list_routines](../../backend/app/agentive/api/routines.py) |
| DELETE | `/agentive/routines/{routine_id}` | [delete_routine](../../backend/app/agentive/api/routines.py) |
| GET | `/agentive/routines/{routine_id}` | [get_routine](../../backend/app/agentive/api/routines.py) |
| PATCH | `/agentive/routines/{routine_id}` | [patch_routine](../../backend/app/agentive/api/routines.py) |
| GET | `/agentive/routines/{routine_id}/activity` | [get_routine_activity](../../backend/app/agentive/api/routines.py) |
| POST | `/agentive/routines/{routine_id}/cancel` | [cancel_routine](../../backend/app/agentive/api/routines.py) |

## speech

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/agentive/speech/config` | [get_speech_config](../../backend/app/agentive/api/speech.py) |
| POST | `/agentive/speech/session` | [create_speech_session](../../backend/app/agentive/api/speech.py) |

## staging

| Methods | Declared path | Handler |
|---|---|---|
| POST | `/agentive/staging/bless-token` | [bless_token_endpoint](../../backend/app/agentive/api/staging.py) |
| GET | `/agentive/staging/pending` | [pending_endpoint](../../backend/app/agentive/api/staging.py) |
| GET | `/agentive/staging/render-pending` | [render_pending_endpoint](../../backend/app/agentive/api/staging.py) |
| GET | `/agentive/staging/render/{token}` | [render_token_endpoint](../../backend/app/agentive/api/staging.py) |
| POST | `/agentive/staging/revoke-token` | [revoke_token_endpoint](../../backend/app/agentive/api/staging.py) |
| GET | `/agentive/staging/rollback-status/{token}` | [rollback_status_endpoint](../../backend/app/agentive/api/staging.py) |
| POST | `/agentive/staging/rollback-token` | [rollback_token_endpoint](../../backend/app/agentive/api/staging.py) |
| POST | `/agentive/staging/text-approve` | [text_approve_endpoint](../../backend/app/agentive/api/staging.py) |
| GET | `/agentive/staging/token/{token}` | [token_state_endpoint](../../backend/app/agentive/api/staging.py) |

## status

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/agentive/status` | [get_agentive_status](../../backend/app/agentive/api/status.py) |

## uplink

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/agentive/uplink/heartbeat` | [agent_heartbeat](../../backend/app/agentive/api/uplink.py) |
| GET | `/agentive/uplink/heartbeat-system` | [agent_heartbeat_system](../../backend/app/agentive/api/uplink.py) |
| POST | `/agentive/uplink/register` | [register_agent](../../backend/app/agentive/api/uplink.py) |
| POST | `/agentive/uplink/register-system` | [register_system_agent](../../backend/app/agentive/api/uplink.py) |
| POST | `/agentive/uplink/unregister` | [unregister_agent](../../backend/app/agentive/api/uplink.py) |

## access

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/apps/{app_id}/access` | [get_space_access](../../backend/app/api/access.py) |
| POST | `/apps/{app_id}/exclusions` | [add_space_exclusion](../../backend/app/api/access.py) |
| DELETE | `/apps/{app_id}/exclusions/{user_id_to_restore}` | [remove_space_exclusion](../../backend/app/api/access.py) |
| GET | `/entries/{entry_id}/access` | [get_entry_access](../../backend/app/api/access.py) |
| POST | `/entries/{entry_id}/collaborators` | [add_entry_collaborator](../../backend/app/api/access.py) |
| DELETE | `/entries/{entry_id}/collaborators/{collaborator_user_id}` | [remove_entry_collaborator](../../backend/app/api/access.py) |
| PATCH | `/entries/{entry_id}/collaborators/{collaborator_user_id}` | [update_entry_collaborator_role](../../backend/app/api/access.py) |
| POST | `/entries/{entry_id}/exclusions` | [add_entry_exclusion](../../backend/app/api/access.py) |
| DELETE | `/entries/{entry_id}/exclusions/{user_id_to_restore}` | [remove_entry_exclusion](../../backend/app/api/access.py) |
| GET | `/tracks/{track_id}/access` | [get_track_access](../../backend/app/api/access.py) |

## admin_packages

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/admin/packages` | [list_loaded_profiles](../../backend/app/api/admin_packages.py) |
| POST | `/admin/packages/rescan` | [rescan_packages](../../backend/app/api/admin_packages.py) |
| GET | `/admin/packages/{slug}/installed` | [list_workspaces_with_bundle](../../backend/app/api/admin_packages.py) |

## admin_users

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/admin/overview` | [admin_overview](../../backend/app/api/admin_users.py) |
| GET | `/admin/users` | [admin_list_users](../../backend/app/api/admin_users.py) |
| POST | `/admin/users` | [admin_create_user](../../backend/app/api/admin_users.py) |
| GET | `/admin/users/{user_id}` | [admin_get_user](../../backend/app/api/admin_users.py) |
| PATCH | `/admin/users/{user_id}` | [admin_patch_user](../../backend/app/api/admin_users.py) |
| POST | `/admin/users/{user_id}/deactivate` | [admin_deactivate_user](../../backend/app/api/admin_users.py) |
| POST | `/admin/users/{user_id}/delete` | [admin_delete_user](../../backend/app/api/admin_users.py) |
| GET | `/admin/users/{user_id}/deletion-preview` | [admin_user_deletion_preview](../../backend/app/api/admin_users.py) |
| POST | `/admin/users/{user_id}/demote-admin` | [admin_demote_user](../../backend/app/api/admin_users.py) |
| POST | `/admin/users/{user_id}/promote-admin` | [admin_promote_user](../../backend/app/api/admin_users.py) |
| POST | `/admin/users/{user_id}/reactivate` | [admin_reactivate_user](../../backend/app/api/admin_users.py) |
| POST | `/admin/users/{user_id}/send-password-reset` | [admin_send_password_reset](../../backend/app/api/admin_users.py) |

## admin_workspaces

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/admin/apps` | [admin_list_apps](../../backend/app/api/admin_workspaces.py) |
| GET | `/admin/apps/{app_id}` | [admin_get_app](../../backend/app/api/admin_workspaces.py) |
| GET | `/admin/tracks` | [admin_list_tracks](../../backend/app/api/admin_workspaces.py) |
| GET | `/admin/tracks/{track_id}` | [admin_get_track](../../backend/app/api/admin_workspaces.py) |
| GET | `/admin/workspaces` | [admin_list_workspaces](../../backend/app/api/admin_workspaces.py) |
| DELETE | `/admin/workspaces/{workspace_id}` | [admin_delete_workspace](../../backend/app/api/admin_workspaces.py) |
| GET | `/admin/workspaces/{workspace_id}` | [admin_get_workspace](../../backend/app/api/admin_workspaces.py) |
| PATCH | `/admin/workspaces/{workspace_id}` | [admin_patch_workspace](../../backend/app/api/admin_workspaces.py) |
| GET | `/admin/workspaces/{workspace_id}/members` | [admin_list_workspace_members](../../backend/app/api/admin_workspaces.py) |

## agent_preferences

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/workspaces/{workspace_id}/agent-preference` | [get_agent_preference](../../backend/app/api/agent_preferences.py) |
| PUT | `/workspaces/{workspace_id}/agent-preference` | [put_agent_preference](../../backend/app/api/agent_preferences.py) |

## ai_chat

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/chat/providers` | [list_providers](../../backend/app/api/ai_chat.py) |
| GET | `/chat/providers/{provider_id}/agents` | [list_provider_agents](../../backend/app/api/ai_chat.py) |
| GET | `/chat/runs/{run_id}/qualification-export` | [export_qualification_run](../../backend/app/api/ai_chat.py) |
| GET | `/chat/threads` | [list_threads](../../backend/app/api/ai_chat.py) |
| POST | `/chat/threads` | [create_thread](../../backend/app/api/ai_chat.py) |
| DELETE | `/chat/threads/{thread_id}` | [delete_thread](../../backend/app/api/ai_chat.py) |
| GET | `/chat/threads/{thread_id}` | [get_thread](../../backend/app/api/ai_chat.py) |
| PATCH | `/chat/threads/{thread_id}` | [rename_thread](../../backend/app/api/ai_chat.py) |
| POST | `/chat/threads/{thread_id}/agent-turn` | [agent_turn](../../backend/app/api/ai_chat.py) |
| POST | `/chat/threads/{thread_id}/cancel` | [cancel_thread](../../backend/app/api/ai_chat.py) |
| GET | `/chat/threads/{thread_id}/harness-sessions/{session_id}/export` | [export_harness_session_for_thread](../../backend/app/api/ai_chat.py) |
| POST | `/chat/threads/{thread_id}/messages` | [send_message](../../backend/app/api/ai_chat.py) |
| POST | `/chat/threads/{thread_id}/system-message` | [append_system_message](../../backend/app/api/ai_chat.py) |
| POST | `/chat/threads/{thread_id}/work-items/{work_item_id}/cancel` | [cancel_chat_turn](../../backend/app/api/ai_chat.py) |
| GET | `/chat/threads/{thread_id}/work-items/{work_item_id}/events` | [replay_chat_turn_events](../../backend/app/api/ai_chat.py) |
| GET | `/chat/threads/{thread_id}/work-items/{work_item_id}/stream` | [stream_chat_turn_events](../../backend/app/api/ai_chat.py) |

## app_extensions

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/extension-view-frame` | [extension_view_frame_route](../../backend/app/api/app_extensions.py) |
| GET | `/extensions/{app_id}/operations` | [list_operations](../../backend/app/api/app_extensions.py) |
| POST | `/extensions/{app_id}/operations/{operation_key}` | [invoke_operation](../../backend/app/api/app_extensions.py) |
| GET | `/extensions/{app_id}/views` | [list_extension_views_route](../../backend/app/api/app_extensions.py) |
| GET | `/extensions/{app_id}/views/{view_key}/handshake` | [extension_view_handshake_route](../../backend/app/api/app_extensions.py) |
| GET | `/extensions/{app_id}/views/{view_key}/{asset_path:path}` | [serve_extension_view_asset_route](../../backend/app/api/app_extensions.py) |

## approvals

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/approvals` | [list_approvals](../../backend/app/api/approvals.py) |
| POST | `/approvals/{approval_id}/approve` | [approve_approval](../../backend/app/api/approvals.py) |
| POST | `/approvals/{approval_id}/reject` | [reject_approval_endpoint](../../backend/app/api/approvals.py) |

## apps

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/apps` | [list_apps](../../backend/app/api/apps.py) |
| POST | `/apps` | [create_app](../../backend/app/api/apps.py) |
| DELETE | `/apps/{app_id}` | [delete_app](../../backend/app/api/apps.py) |
| GET | `/apps/{app_id}` | [get_app](../../backend/app/api/apps.py) |
| PUT | `/apps/{app_id}` | [update_app](../../backend/app/api/apps.py) |
| GET | `/apps/{app_id}/collaborators` | [list_app_collaborators](../../backend/app/api/apps.py) |
| POST | `/apps/{app_id}/collaborators` | [add_app_collaborator](../../backend/app/api/apps.py) |
| DELETE | `/apps/{app_id}/collaborators/{collaborator_user_id}` | [remove_app_collaborator](../../backend/app/api/apps.py) |
| PATCH | `/apps/{app_id}/collaborators/{collaborator_user_id}` | [update_app_collaborator_role](../../backend/app/api/apps.py) |
| GET | `/apps/{app_id}/definition` | [get_app_definition](../../backend/app/api/apps.py) |
| GET | `/apps/{app_id}/definition/preview` | [preview_app_definition](../../backend/app/api/apps.py) |
| POST | `/apps/{app_id}/definition/verify` | [verify_app_definition](../../backend/app/api/apps.py) |
| GET | `/apps/{app_id}/export` | [export_app](../../backend/app/api/apps.py) |
| POST | `/apps/{app_id}/install/settings` | [finalize_install_endpoint](../../backend/app/api/apps.py) |
| GET | `/apps/{app_id}/operational-model` | [get_app_operational_model](../../backend/app/api/apps.py) |
| PATCH | `/apps/{app_id}/operational-model` | [patch_app_operational_model](../../backend/app/api/apps.py) |
| POST | `/apps/{app_id}/operational-model/apply` | [apply_app_operational_model_library](../../backend/app/api/apps.py) |
| POST | `/apps/{app_id}/operational-model/merge-library` | [merge_library_into_app_operational_model](../../backend/app/api/apps.py) |
| POST | `/apps/{app_id}/operational-model/preview` | [preview_app_operational_model_merge](../../backend/app/api/apps.py) |
| GET | `/apps/{app_id}/operational-model/track-templates` | [list_app_track_templates](../../backend/app/api/apps.py) |
| POST | `/apps/{app_id}/operational-model/track-templates` | [create_app_track_template](../../backend/app/api/apps.py) |
| DELETE | `/apps/{app_id}/operational-model/track-templates/{template_id}` | [delete_app_track_template](../../backend/app/api/apps.py) |
| POST | `/apps/{app_id}/operational-model/track-templates/{template_id}/apply-to-track` | [apply_track_template_to_track](../../backend/app/api/apps.py) |
| POST | `/apps/{app_id}/operational-model/track-templates/{template_id}/entry-types` | [create_track_template_entry_type](../../backend/app/api/apps.py) |
| POST | `/apps/{app_id}/operational-model/track-templates/{template_id}/tags` | [create_track_template_tag](../../backend/app/api/apps.py) |
| POST | `/apps/{app_id}/operational-model/track-templates/{template_id}/views` | [create_track_template_view](../../backend/app/api/apps.py) |
| POST | `/apps/{app_id}/pause` | [pause_app_endpoint](../../backend/app/api/apps.py) |
| POST | `/apps/{app_id}/resume` | [resume_app_endpoint](../../backend/app/api/apps.py) |
| GET | `/apps/{app_id}/settings` | [get_app_settings](../../backend/app/api/apps.py) |
| PATCH | `/apps/{app_id}/settings` | [patch_app_settings](../../backend/app/api/apps.py) |
| GET | `/apps/{app_id}/tracks` | [list_app_tracks](../../backend/app/api/apps.py) |
| POST | `/apps/{app_id}/tracks` | [add_track_to_app](../../backend/app/api/apps.py) |
| PATCH | `/apps/{app_id}/tracks/order` | [reorder_app_tracks](../../backend/app/api/apps.py) |
| DELETE | `/apps/{app_id}/tracks/{track_id}` | [remove_track_from_app](../../backend/app/api/apps.py) |
| POST | `/apps/{app_id}/transfer-ownership` | [post_transfer_app_ownership](../../backend/app/api/apps.py) |
| POST | `/apps/{app_id}/uninstall` | [uninstall_app_endpoint](../../backend/app/api/apps.py) |
| GET | `/apps/{app_id}/uninstall-preflight` | [uninstall_preflight_endpoint](../../backend/app/api/apps.py) |
| POST | `/apps/{app_id}/update-from-library` | [update_from_library_endpoint](../../backend/app/api/apps.py) |
| POST | `/workspaces/{workspace_id}/apps/install` | [install_app_endpoint](../../backend/app/api/apps.py) |

## apps_batch_install

| Methods | Declared path | Handler |
|---|---|---|
| POST | `/apps/batch-install` | [batch_install_apps](../../backend/app/api/apps_batch_install.py) |

## apps_dashboards

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/apps/{app_id}/dashboards` | [list_app_dashboards](../../backend/app/api/apps_dashboards.py) |
| POST | `/apps/{app_id}/dashboards` | [create_app_dashboard](../../backend/app/api/apps_dashboards.py) |
| GET | `/apps/{app_id}/dashboards/suggest` | [suggest_app_dashboard](../../backend/app/api/apps_dashboards.py) |
| DELETE | `/apps/{app_id}/dashboards/{dashboard_id}` | [delete_app_dashboard](../../backend/app/api/apps_dashboards.py) |
| GET | `/apps/{app_id}/dashboards/{dashboard_id}` | [get_app_dashboard](../../backend/app/api/apps_dashboards.py) |
| PATCH | `/apps/{app_id}/dashboards/{dashboard_id}` | [patch_app_dashboard](../../backend/app/api/apps_dashboards.py) |
| GET | `/apps/{app_id}/dashboards/{dashboard_id}/data` | [get_app_dashboard_data](../../backend/app/api/apps_dashboards.py) |
| POST | `/apps/{app_id}/dashboards/{dashboard_id}/drill-through` | [get_dashboard_widget_result_set](../../backend/app/api/apps_dashboards.py) |
| GET | `/apps/{app_id}/home` | [get_app_home](../../backend/app/api/apps_dashboards.py) |
| GET | `/dashboard-widget-substrate` | [get_dashboard_widget_substrate](../../backend/app/api/apps_dashboards.py) |

## apps_skills

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/apps/{slug}/skills` | [list_app_skills](../../backend/app/api/apps_skills.py) |

## attachments

| Methods | Declared path | Handler |
|---|---|---|
| DELETE | `/attachments/{attachment_id}` | [delete_attachment](../../backend/app/api/attachments.py) |
| GET | `/attachments/{attachment_id}` | [download_attachment](../../backend/app/api/attachments.py) |
| GET | `/attachments/{attachment_id}/download` | [stream_attachment](../../backend/app/api/attachments.py) |
| GET | `/attachments/{attachment_id}/preview` | [stream_attachment_preview](../../backend/app/api/attachments.py) |
| POST | `/attachments/{attachment_id}/reprocess` | [reprocess_attachment_metadata](../../backend/app/api/attachments.py) |
| GET | `/attachments/{attachment_id}/thumb` | [stream_attachment_thumbnail](../../backend/app/api/attachments.py) |
| POST | `/chat/threads/{thread_id}/attachments` | [upload_chat_attachment](../../backend/app/api/attachments.py) |
| GET | `/entries/{entry_id}/attachments` | [list_entry_attachments](../../backend/app/api/attachments.py) |
| POST | `/entries/{entry_id}/attachments` | [upload_attachment](../../backend/app/api/attachments.py) |
| POST | `/entries/{entry_id}/attachments/batch` | [upload_attachments_batch](../../backend/app/api/attachments.py) |
| GET | `/entries/{entry_id}/attachments/download-all` | [download_entry_attachments_zip](../../backend/app/api/attachments.py) |
| POST | `/entries/{entry_id}/attachments/url` | [create_url_attachment](../../backend/app/api/attachments.py) |
| POST | `/entries/{entry_id}/uploads` | [start_chunked_upload](../../backend/app/api/attachments.py) |
| DELETE | `/uploads/{session_id}` | [cancel_chunked_upload](../../backend/app/api/attachments.py) |
| GET | `/uploads/{session_id}` | [get_chunked_upload_session](../../backend/app/api/attachments.py) |
| PUT | `/uploads/{session_id}/chunks/{chunk_index}` | [append_chunked_upload_part](../../backend/app/api/attachments.py) |
| POST | `/uploads/{session_id}/complete` | [complete_chunked_upload](../../backend/app/api/attachments.py) |

## audit_log

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/audit-log` | [list_audit_log](../../backend/app/api/audit_log.py) |

## auth

| Methods | Declared path | Handler |
|---|---|---|
| POST | `/auth/complete-assigned-form` | [complete_assigned_form](../../backend/app/api/auth.py) |
| POST | `/auth/complete-onboarding-form` | [complete_onboarding_form](../../backend/app/api/auth.py) |
| POST | `/auth/forgot-password` | [forgot_password](../../backend/app/api/auth.py) |
| GET | `/auth/me` | [get_current_user](../../backend/app/api/auth.py) |
| POST | `/auth/resend-verification` | [resend_verification](../../backend/app/api/auth.py) |
| POST | `/auth/reset-password` | [reset_password](../../backend/app/api/auth.py) |
| POST | `/auth/signup` | [register_user](../../backend/app/api/auth.py) |
| POST | `/auth/update-password` | [update_password](../../backend/app/api/auth.py) |
| PUT | `/auth/update-profile` | [update_profile](../../backend/app/api/auth.py) |
| POST | `/auth/verify-email` | [verify_email](../../backend/app/api/auth.py) |
| POST | `/auth/ws-ticket` | [mint_websocket_ticket](../../backend/app/api/auth.py) |

## capabilities

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/capabilities` | [list_capabilities](../../backend/app/api/capabilities.py) |
| GET | `/extensions/{app_id}/queries` | [list_extension_queries](../../backend/app/api/capabilities.py) |
| POST | `/extensions/{app_id}/queries/{query_key}` | [invoke_extension_query](../../backend/app/api/capabilities.py) |
| POST | `/query` | [post_query](../../backend/app/api/capabilities.py) |

## comments

| Methods | Declared path | Handler |
|---|---|---|
| DELETE | `/comments/{comment_id}` | [delete_comment](../../backend/app/api/comments.py) |
| PUT | `/comments/{comment_id}` | [update_comment](../../backend/app/api/comments.py) |
| GET | `/entries/{entry_id}/comments` | [get_entry_comments](../../backend/app/api/comments.py) |
| POST | `/entries/{entry_id}/comments` | [create_comment](../../backend/app/api/comments.py) |

## conflicts

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/conflicts` | [list_all](../../backend/app/api/conflicts.py) |
| GET | `/conflicts/{conflict_id}` | [get_one](../../backend/app/api/conflicts.py) |
| POST | `/conflicts/{conflict_id}/resolve` | [resolve](../../backend/app/api/conflicts.py) |

## connectors

| Methods | Declared path | Handler |
|---|---|---|
| POST | `/connectors/{connector_id}/sync` | [sync_connector](../../backend/app/api/connectors.py) |

## entitlements

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/entitlements` | [get_list_entitlements](../../backend/app/api/entitlements.py) |
| POST | `/entitlements/grant` | [post_grant_entitlement](../../backend/app/api/entitlements.py) |
| POST | `/entitlements/revoke` | [post_revoke_entitlement](../../backend/app/api/entitlements.py) |

## entries

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/entries` | [list_entries](../../backend/app/api/entries.py) |
| POST | `/entries` | [create_entry](../../backend/app/api/entries.py) |
| DELETE | `/entries/{entry_id}` | [delete_entry](../../backend/app/api/entries.py) |
| GET | `/entries/{entry_id}` | [get_entry](../../backend/app/api/entries.py) |
| PUT | `/entries/{entry_id}` | [update_entry](../../backend/app/api/entries.py) |
| POST | `/entries/{entry_id}/reactions` | [add_reaction](../../backend/app/api/entries.py) |
| DELETE | `/entries/{entry_id}/reactions/{emoji}` | [remove_reaction](../../backend/app/api/entries.py) |
| POST | `/entries/{entry_id}/tags` | [add_tag_to_entry](../../backend/app/api/entries.py) |
| DELETE | `/entries/{entry_id}/tags/{tag_id}` | [remove_tag_from_entry](../../backend/app/api/entries.py) |
| POST | `/entries/{entry_id}/unwatch` | [unwatch_entry](../../backend/app/api/entries.py) |
| POST | `/entries/{entry_id}/watch` | [watch_entry](../../backend/app/api/entries.py) |
| GET | `/entries/{entry_id}/watchers` | [get_entry_watchers](../../backend/app/api/entries.py) |

## entries_precompute

| Methods | Declared path | Handler |
|---|---|---|
| POST | `/entries/{entry_id}/precompute` | [precompute_entry](../../backend/app/api/entries_precompute.py) |

## entries_public_share

| Methods | Declared path | Handler |
|---|---|---|
| POST | `/entries/{entry_id}/public-share` | [mint_public_share](../../backend/app/api/entries_public_share.py) |
| GET | `/portfolio/shared/{token}` | [get_public_share_legacy_alias](../../backend/app/api/entries_public_share.py) |
| GET | `/public-share/{token}` | [get_public_share](../../backend/app/api/entries_public_share.py) |

## entries_transform

| Methods | Declared path | Handler |
|---|---|---|
| POST | `/entries/{entry_id}/transform` | [transform_entry](../../backend/app/api/entries_transform.py) |

## entry_lookup

| Methods | Declared path | Handler |
|---|---|---|
| POST | `/entry-lookup` | [relation_target_lookup](../../backend/app/api/entry_lookup.py) |

## entry_relations

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/entries/{entry_id}/related` | [list_entry_relations](../../backend/app/api/entry_relations.py) |
| POST | `/entries/{entry_id}/related/link` | [link_entry_relation](../../backend/app/api/entry_relations.py) |
| DELETE | `/entries/{entry_id}/related/{source_id}` | [unlink_entry_relation](../../backend/app/api/entry_relations.py) |

## entry_types

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/entry-types` | [list_entry_types](../../backend/app/api/entry_types.py) |
| POST | `/entry-types` | [create_entry_type](../../backend/app/api/entry_types.py) |
| DELETE | `/entry-types/{entry_type_id}` | [delete_entry_type](../../backend/app/api/entry_types.py) |
| GET | `/entry-types/{entry_type_id}` | [get_entry_type](../../backend/app/api/entry_types.py) |
| PUT | `/entry-types/{entry_type_id}` | [update_entry_type](../../backend/app/api/entry_types.py) |

## events_polling

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/events` | [list_events_polling](../../backend/app/api/events_polling.py) |

## feed

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/feed` | [get_feed](../../backend/app/api/feed.py) |
| GET | `/feed_entries` | [get_feed_entries](../../backend/app/api/feed.py) |
| POST | `/feed_entries` | [create_feed_entry](../../backend/app/api/feed.py) |
| DELETE | `/feed_entries/{entry_id}` | [delete_feed_entry](../../backend/app/api/feed.py) |
| PUT | `/feed_entries/{entry_id}` | [update_feed_entry](../../backend/app/api/feed.py) |

## invitations

| Methods | Declared path | Handler |
|---|---|---|
| POST | `/apps/{app_id}/invitations` | [post_create_space_invitation](../../backend/app/api/invitations.py) |
| POST | `/entries/{entry_id}/invitations` | [post_create_entry_invitation](../../backend/app/api/invitations.py) |
| DELETE | `/invitations/{invitation_id}` | [delete_resource_invitation](../../backend/app/api/invitations.py) |
| GET | `/invitations/{token}` | [get_invitation_preview](../../backend/app/api/invitations.py) |
| POST | `/invitations/{token}/accept` | [post_accept_invitation](../../backend/app/api/invitations.py) |
| POST | `/invitations/{token}/decline` | [post_decline_invitation](../../backend/app/api/invitations.py) |
| POST | `/tracks/{track_id}/invitations` | [post_create_track_invitation](../../backend/app/api/invitations.py) |
| GET | `/workspaces/{workspace_id}/invitations` | [list_invitations](../../backend/app/api/invitations.py) |
| POST | `/workspaces/{workspace_id}/invitations` | [post_create_invitation](../../backend/app/api/invitations.py) |
| DELETE | `/workspaces/{workspace_id}/invitations/{invitation_id}` | [delete_invitation](../../backend/app/api/invitations.py) |

## link_preview

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/link-preview` | [get_link_preview](../../backend/app/api/link_preview.py) |

## me_assigned_form

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/me/assigned-form` | [get_me_assigned_form](../../backend/app/api/me_assigned_form.py) |
| PATCH | `/me/assigned-form` | [patch_me_assigned_form](../../backend/app/api/me_assigned_form.py) |
| POST | `/me/assigned-form/attachments` | [upload_me_assigned_form_attachment](../../backend/app/api/me_assigned_form.py) |
| GET | `/me/assigned-form/contract` | [download_me_assigned_form_contract](../../backend/app/api/me_assigned_form.py) |
| POST | `/me/assigned-form/contract/decision` | [decide_me_assigned_form_contract](../../backend/app/api/me_assigned_form.py) |
| POST | `/me/assigned-form/contract/generate` | [generate_me_assigned_form_contract](../../backend/app/api/me_assigned_form.py) |
| GET | `/me/assigned-form/policies` | [list_me_assigned_form_policies](../../backend/app/api/me_assigned_form.py) |
| GET | `/me/assigned-form/policies/{policy_entry_id}/document` | [download_me_assigned_form_policy_document](../../backend/app/api/me_assigned_form.py) |

## me_onboarding_form

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/me/onboarding-form` | [get_me_onboarding_form](../../backend/app/api/me_onboarding_form.py) |
| PATCH | `/me/onboarding-form` | [patch_me_onboarding_form](../../backend/app/api/me_onboarding_form.py) |
| POST | `/me/onboarding-form/attachments` | [upload_me_onboarding_attachment](../../backend/app/api/me_onboarding_form.py) |
| GET | `/me/onboarding-form/contract` | [download_me_onboarding_contract](../../backend/app/api/me_onboarding_form.py) |
| POST | `/me/onboarding-form/contract/decision` | [decide_me_onboarding_contract](../../backend/app/api/me_onboarding_form.py) |
| POST | `/me/onboarding-form/contract/generate` | [generate_me_onboarding_contract](../../backend/app/api/me_onboarding_form.py) |
| GET | `/me/onboarding-form/onboarding-policies` | [list_me_onboarding_policies](../../backend/app/api/me_onboarding_form.py) |
| GET | `/me/onboarding-form/onboarding-policies/{policy_entry_id}/document` | [download_me_onboarding_policy_document](../../backend/app/api/me_onboarding_form.py) |

## meta

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/health/ready` | [readiness](../../backend/app/api/meta.py) |
| GET | `/meta/build` | [build_meta](../../backend/app/api/meta.py) |
| GET | `/meta/colors` | [get_color_palettes](../../backend/app/api/meta.py) |
| GET | `/meta/entry-type-icons` | [get_entry_type_icons](../../backend/app/api/meta.py) |
| GET | `/meta/templates` | [get_templates](../../backend/app/api/meta.py) |

## mission_control

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/me/mission-control` | [get_mission_control_snapshot](../../backend/app/api/mission_control.py) |

## model_credentials

| Methods | Declared path | Handler |
|---|---|---|
| DELETE | `/users/me/model-credentials` | [delete_my_model_credential](../../backend/app/api/model_credentials.py) |
| GET | `/users/me/model-credentials` | [get_my_model_credential](../../backend/app/api/model_credentials.py) |
| POST | `/users/me/model-credentials` | [upsert_my_model_credential](../../backend/app/api/model_credentials.py) |
| POST | `/users/me/model-credentials/validate` | [validate_my_model_credential](../../backend/app/api/model_credentials.py) |

## notification_preferences

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/users/me/notification-preferences` | [get_notification_preferences](../../backend/app/api/notification_preferences.py) |
| PATCH | `/users/me/notification-preferences` | [patch_notification_preferences](../../backend/app/api/notification_preferences.py) |

## notifications

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/notifications` | [get_notifications](../../backend/app/api/notifications.py) |
| POST | `/notifications` | [create_notification](../../backend/app/api/notifications.py) |
| PUT | `/notifications/mark-all-read` | [mark_all_notifications_as_read](../../backend/app/api/notifications.py) |
| DELETE | `/notifications/{notification_id}` | [delete_notification](../../backend/app/api/notifications.py) |
| PUT | `/notifications/{notification_id}/read` | [mark_notification_as_read](../../backend/app/api/notifications.py) |
| POST | `/notifications/{notification_id}/respond` | [respond_to_notification](../../backend/app/api/notifications.py) |

## oauth_consent

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/oauth/consent` | [get_consent](../../backend/app/api/oauth_consent.py) |
| POST | `/oauth/consent/approve` | [approve_consent](../../backend/app/api/oauth_consent.py) |

## operational_models

| Methods | Declared path | Handler |
|---|---|---|
| POST | `/apps/{app_id}/operational-model/detach-library` | [detach_library_from_space_profile](../../backend/app/api/operational_models.py) |
| POST | `/apps/{app_id}/operational-model/revert-customizations` | [revert_space_profile_customizations](../../backend/app/api/operational_models.py) |
| GET | `/operational-model-substrate` | [get_operational_model_substrate](../../backend/app/api/operational_models.py) |
| GET | `/operational-models` | [list_library_operational_models](../../backend/app/api/operational_models.py) |
| POST | `/operational-models` | [publish_operational_model](../../backend/app/api/operational_models.py) |
| POST | `/operational-models/author` | [author_operational_model](../../backend/app/api/operational_models.py) |
| POST | `/operational-models/from-app/{app_id}` | [derive_library_profile_from_space](../../backend/app/api/operational_models.py) |
| POST | `/operational-models/from-track/{track_id}` | [derive_library_profile_from_track](../../backend/app/api/operational_models.py) |
| POST | `/operational-models/import` | [import_operational_model](../../backend/app/api/operational_models.py) |
| POST | `/operational-models/validate` | [validate_operational_model_manifest](../../backend/app/api/operational_models.py) |
| DELETE | `/operational-models/{operational_model_id}` | [deprecate_library_operational_model](../../backend/app/api/operational_models.py) |
| GET | `/operational-models/{operational_model_id}` | [get_library_operational_model](../../backend/app/api/operational_models.py) |
| PUT | `/operational-models/{operational_model_id}` | [update_library_operational_model](../../backend/app/api/operational_models.py) |
| POST | `/operational-models/{operational_model_id}/diff` | [diff_operational_model](../../backend/app/api/operational_models.py) |
| POST | `/operational-models/{operational_model_id}/discard-draft` | [discard_operational_model_draft](../../backend/app/api/operational_models.py) |
| POST | `/operational-models/{operational_model_id}/draft` | [fork_operational_model_draft](../../backend/app/api/operational_models.py) |
| GET | `/operational-models/{operational_model_id}/migration-status` | [get_operational_model_migration_status](../../backend/app/api/operational_models.py) |
| POST | `/operational-models/{operational_model_id}/modify` | [modify_operational_model](../../backend/app/api/operational_models.py) |
| POST | `/operational-models/{operational_model_id}/preview-update` | [preview_update_operational_model](../../backend/app/api/operational_models.py) |
| POST | `/operational-models/{operational_model_id}/publish` | [publish_operational_model_draft](../../backend/app/api/operational_models.py) |
| POST | `/operational-models/{operational_model_id}/retry-migration` | [retry_operational_model_migration](../../backend/app/api/operational_models.py) |
| POST | `/tracks/{track_id}/operational-model/detach-library` | [detach_library_from_track_profile](../../backend/app/api/operational_models.py) |
| POST | `/tracks/{track_id}/operational-model/entry-types` | [add_entry_type_to_track_profile](../../backend/app/api/operational_models.py) |
| DELETE | `/tracks/{track_id}/operational-model/entry-types/{entry_type_id}` | [remove_entry_type_from_track_profile](../../backend/app/api/operational_models.py) |
| POST | `/tracks/{track_id}/operational-model/revert-customizations` | [revert_track_profile_customizations](../../backend/app/api/operational_models.py) |
| POST | `/tracks/{track_id}/operational-model/views` | [add_view_to_track_profile](../../backend/app/api/operational_models.py) |
| DELETE | `/tracks/{track_id}/operational-model/views/{view_id}` | [remove_view_from_track_profile](../../backend/app/api/operational_models.py) |
| GET | `/workspaces/{workspace_id}/operational-models` | [list_workspace_operational_models](../../backend/app/api/operational_models.py) |

## policies

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/policies` | [list_policies](../../backend/app/api/policies.py) |
| POST | `/policies` | [post_create_policy](../../backend/app/api/policies.py) |
| POST | `/policies/explain` | [explain_action](../../backend/app/api/policies.py) |
| DELETE | `/policies/{policy_id}` | [delete_policy_by_id](../../backend/app/api/policies.py) |
| GET | `/policies/{policy_id}` | [get_policy_by_id](../../backend/app/api/policies.py) |
| PATCH | `/policies/{policy_id}` | [patch_policy](../../backend/app/api/policies.py) |

## query_spec

| Methods | Declared path | Handler |
|---|---|---|
| POST | `/query-spec` | [execute_query_spec_endpoint](../../backend/app/api/query_spec.py) |

## retrieval_config

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/retrieval/config` | [get_retrieval_config](../../backend/app/api/retrieval_config.py) |

## retrieve

| Methods | Declared path | Handler |
|---|---|---|
| POST | `/retrieve` | [retrieve](../../backend/app/api/retrieve.py) |

## shared_with_me

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/me/invitations` | [list_my_invitations](../../backend/app/api/shared_with_me.py) |
| POST | `/me/invitations/{invitation_id}/accept` | [post_accept_my_invitation](../../backend/app/api/shared_with_me.py) |
| POST | `/me/invitations/{invitation_id}/decline` | [post_decline_my_invitation](../../backend/app/api/shared_with_me.py) |
| GET | `/me/shared` | [list_shared_with_me](../../backend/app/api/shared_with_me.py) |
| GET | `/me/sharing-overview` | [get_sharing_overview](../../backend/app/api/shared_with_me.py) |

## shares

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/apps/{app_id}/shares` | [list_space_links](../../backend/app/api/shares.py) |
| POST | `/apps/{app_id}/shares` | [mint_space_link](../../backend/app/api/shares.py) |
| GET | `/entries/{entry_id}/shares` | [list_entry_links](../../backend/app/api/shares.py) |
| POST | `/entries/{entry_id}/shares` | [mint_entry_link](../../backend/app/api/shares.py) |
| POST | `/shares/redeem` | [redeem_link](../../backend/app/api/shares.py) |
| DELETE | `/shares/{share_link_id}` | [revoke_link](../../backend/app/api/shares.py) |
| GET | `/tracks/{track_id}/shares` | [list_track_links](../../backend/app/api/shares.py) |
| POST | `/tracks/{track_id}/shares` | [mint_track_link](../../backend/app/api/shares.py) |

## speech_preferences

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/users/me/speech-preferences` | [get_my_speech_preferences](../../backend/app/api/speech_preferences.py) |
| PATCH | `/users/me/speech-preferences` | [patch_my_speech_preferences](../../backend/app/api/speech_preferences.py) |

## tags

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/tags` | [list_tags](../../backend/app/api/tags.py) |
| POST | `/tags` | [create_tag](../../backend/app/api/tags.py) |
| DELETE | `/tags/{tag_id}` | [delete_tag](../../backend/app/api/tags.py) |
| GET | `/tags/{tag_id}` | [get_tag](../../backend/app/api/tags.py) |
| PUT | `/tags/{tag_id}` | [update_tag](../../backend/app/api/tags.py) |
| GET | `/tags/{tag_id}/children` | [list_tag_children](../../backend/app/api/tags.py) |
| GET | `/tags/{tag_id}/tree` | [get_tag_tree](../../backend/app/api/tags.py) |

## tools

| Methods | Declared path | Handler |
|---|---|---|
| POST | `/tools/{tool_key}` | [call_tool](../../backend/app/api/tools.py) |

## tracks

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/tracks` | [list_tracks](../../backend/app/api/tracks.py) |
| POST | `/tracks` | [create_track](../../backend/app/api/tracks.py) |
| POST | `/tracks/{target_track_id}/promote-scratch-entry` | [post_promote_scratch_entry](../../backend/app/api/tracks.py) |
| DELETE | `/tracks/{track_id}` | [delete_track](../../backend/app/api/tracks.py) |
| GET | `/tracks/{track_id}` | [get_track](../../backend/app/api/tracks.py) |
| PUT | `/tracks/{track_id}` | [update_track](../../backend/app/api/tracks.py) |
| GET | `/tracks/{track_id}/collaborators` | [list_collaborators](../../backend/app/api/tracks.py) |
| POST | `/tracks/{track_id}/collaborators` | [add_collaborator](../../backend/app/api/tracks.py) |
| DELETE | `/tracks/{track_id}/collaborators/{collaborator_user_id}` | [remove_collaborator](../../backend/app/api/tracks.py) |
| PATCH | `/tracks/{track_id}/collaborators/{collaborator_user_id}` | [update_collaborator_role](../../backend/app/api/tracks.py) |
| GET | `/tracks/{track_id}/detail` | [get_track_detail_bundle](../../backend/app/api/tracks.py) |
| GET | `/tracks/{track_id}/entries` | [get_track_entries](../../backend/app/api/tracks.py) |
| POST | `/tracks/{track_id}/exclusions` | [add_exclusion](../../backend/app/api/tracks.py) |
| DELETE | `/tracks/{track_id}/exclusions/{user_id_to_restore}` | [remove_exclusion](../../backend/app/api/tracks.py) |
| GET | `/tracks/{track_id}/mention-candidates` | [list_mention_candidates](../../backend/app/api/tracks.py) |
| GET | `/tracks/{track_id}/operational-model` | [get_track_operational_model](../../backend/app/api/tracks.py) |
| PATCH | `/tracks/{track_id}/operational-model` | [patch_track_operational_model](../../backend/app/api/tracks.py) |
| POST | `/tracks/{track_id}/operational-model/merge-library` | [merge_library_into_track_operational_model](../../backend/app/api/tracks.py) |
| POST | `/tracks/{track_id}/transfer-ownership` | [post_transfer_track_ownership](../../backend/app/api/tracks.py) |
| POST | `/tracks/{track_id}/unwatch` | [unwatch_track](../../backend/app/api/tracks.py) |
| POST | `/tracks/{track_id}/watch` | [watch_track](../../backend/app/api/tracks.py) |
| GET | `/tracks/{track_id}/watchers` | [get_track_watchers](../../backend/app/api/tracks.py) |

## tracks_public_share

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/public-share/track/{token}` | [get_public_track](../../backend/app/api/tracks_public_share.py) |
| GET | `/public-share/track/{token}/apply` | [get_public_track_apply_context](../../backend/app/api/tracks_public_share.py) |
| GET | `/public-share/track/{token}/entries` | [get_public_track_entries](../../backend/app/api/tracks_public_share.py) |
| POST | `/public-share/track/{token}/entries` | [create_public_track_entry](../../backend/app/api/tracks_public_share.py) |
| GET | `/public-share/track/{token}/entries/{entry_id}` | [get_public_track_entry](../../backend/app/api/tracks_public_share.py) |
| PATCH | `/public-share/track/{token}/entries/{entry_id}` | [update_public_track_entry](../../backend/app/api/tracks_public_share.py) |
| POST | `/public-share/track/{token}/entries/{entry_id}/attachments` | [upload_public_track_entry_attachment](../../backend/app/api/tracks_public_share.py) |
| GET | `/public-share/track/{token}/entries/{entry_id}/comments` | [get_public_track_entry_comments](../../backend/app/api/tracks_public_share.py) |
| POST | `/public-share/track/{token}/entries/{entry_id}/comments` | [create_public_track_entry_comment](../../backend/app/api/tracks_public_share.py) |
| POST | `/public-share/track/{token}/entries/{entry_id}/reactions` | [add_public_track_entry_reaction](../../backend/app/api/tracks_public_share.py) |
| DELETE | `/public-share/track/{token}/entries/{entry_id}/reactions/{emoji}` | [remove_public_track_entry_reaction](../../backend/app/api/tracks_public_share.py) |
| GET | `/public-share/track/{token}/relation-options` | [get_public_track_relation_options](../../backend/app/api/tracks_public_share.py) |
| GET | `/tracks/{track_id}/public-share` | [get_public_track_share_settings](../../backend/app/api/tracks_public_share.py) |
| POST | `/tracks/{track_id}/public-share` | [update_public_track_share_settings](../../backend/app/api/tracks_public_share.py) |
| POST | `/tracks/{track_id}/public-share/notify` | [notify_public_track_share](../../backend/app/api/tracks_public_share.py) |

## tracks_public_share_onboarding

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/public-share/track/{token}/entries/{entry_id}/attachments` | [list_public_track_entry_attachments](../../backend/app/api/tracks_public_share_onboarding.py) |
| GET | `/public-share/track/{token}/entries/{entry_id}/contract` | [download_public_onboarding_contract](../../backend/app/api/tracks_public_share_onboarding.py) |
| POST | `/public-share/track/{token}/entries/{entry_id}/contract/decision` | [decide_public_onboarding_contract](../../backend/app/api/tracks_public_share_onboarding.py) |
| POST | `/public-share/track/{token}/entries/{entry_id}/contract/generate` | [generate_public_onboarding_contract](../../backend/app/api/tracks_public_share_onboarding.py) |
| GET | `/public-share/track/{token}/onboarding-policies` | [list_public_onboarding_policies](../../backend/app/api/tracks_public_share_onboarding.py) |
| GET | `/public-share/track/{token}/onboarding-policies/{policy_entry_id}/document` | [download_public_onboarding_policy_document](../../backend/app/api/tracks_public_share_onboarding.py) |

## user_signatures

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/me/signatures` | [api_list_my_signatures](../../backend/app/api/user_signatures.py) |
| POST | `/me/signatures` | [api_save_my_signature](../../backend/app/api/user_signatures.py) |
| DELETE | `/me/signatures/{attachment_id}` | [api_delete_my_signature](../../backend/app/api/user_signatures.py) |
| GET | `/me/signatures/{attachment_id}/download` | [api_download_my_signature](../../backend/app/api/user_signatures.py) |

## users

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/users` | [list_users](../../backend/app/api/users.py) |
| POST | `/users` | [create_user](../../backend/app/api/users.py) |
| GET | `/users/me/account-deletion-preview` | [get_account_deletion_preview_endpoint](../../backend/app/api/users.py) |
| GET | `/users/me/connected-agents` | [list_connected_agents](../../backend/app/api/users.py) |
| DELETE | `/users/me/connected-agents/{client_id}` | [revoke_connected_agent](../../backend/app/api/users.py) |
| GET | `/users/me/pinned` | [get_my_pinned](../../backend/app/api/users.py) |
| POST | `/users/me/pinned` | [toggle_my_pin](../../backend/app/api/users.py) |
| GET | `/users/me/scope` | [get_my_scope](../../backend/app/api/users.py) |
| PUT | `/users/me/scope` | [set_my_scope](../../backend/app/api/users.py) |
| DELETE | `/users/{user_id}` | [delete_user](../../backend/app/api/users.py) |
| GET | `/users/{user_id}` | [get_user](../../backend/app/api/users.py) |
| PUT | `/users/{user_id}` | [update_user](../../backend/app/api/users.py) |
| GET | `/users/{user_id}/avatar` | [get_avatar](../../backend/app/api/users.py) |
| POST | `/users/{user_id}/avatar` | [upload_avatar](../../backend/app/api/users.py) |

## views

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/tracks/{track_id}/views` | [list_track_views](../../backend/app/api/views.py) |
| POST | `/tracks/{track_id}/views` | [create_view](../../backend/app/api/views.py) |
| DELETE | `/views/{view_id}` | [delete_view](../../backend/app/api/views.py) |
| GET | `/views/{view_id}` | [get_view](../../backend/app/api/views.py) |
| PUT | `/views/{view_id}` | [update_view](../../backend/app/api/views.py) |

## work_items

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/work-items` | [get_work_items](../../backend/app/api/work_items.py) |
| GET | `/work-items/{work_item_id}` | [get_work_item](../../backend/app/api/work_items.py) |

## workspace_member_hire

| Methods | Declared path | Handler |
|---|---|---|
| POST | `/workspaces/{workspace_id}/members/provision` | [provision_workspace_member_route](../../backend/app/api/workspace_member_hire.py) |
| POST | `/workspaces/{workspace_id}/members/{member_user_id}/assigned-form-prompt` | [set_member_assigned_form_prompt_route](../../backend/app/api/workspace_member_hire.py) |
| POST | `/workspaces/{workspace_id}/members/{member_user_id}/onboarding-form-prompt` | [set_member_onboarding_form_prompt_route](../../backend/app/api/workspace_member_hire.py) |

## workspaces

| Methods | Declared path | Handler |
|---|---|---|
| GET | `/library/workspace-models` | [list_workspace_operational_models](../../backend/app/api/workspaces.py) |
| GET | `/workspaces` | [list_workspaces](../../backend/app/api/workspaces.py) |
| POST | `/workspaces` | [create_workspace](../../backend/app/api/workspaces.py) |
| DELETE | `/workspaces/{workspace_id}` | [delete_workspace](../../backend/app/api/workspaces.py) |
| GET | `/workspaces/{workspace_id}` | [get_workspace](../../backend/app/api/workspaces.py) |
| PATCH | `/workspaces/{workspace_id}` | [update_workspace](../../backend/app/api/workspaces.py) |
| GET | `/workspaces/{workspace_id}/apps` | [list_workspace_spaces](../../backend/app/api/workspaces.py) |
| PATCH | `/workspaces/{workspace_id}/apps/order` | [reorder_workspace_apps](../../backend/app/api/workspaces.py) |
| GET | `/workspaces/{workspace_id}/avatar` | [get_workspace_avatar](../../backend/app/api/workspaces.py) |
| POST | `/workspaces/{workspace_id}/avatar` | [upload_workspace_avatar](../../backend/app/api/workspaces.py) |
| GET | `/workspaces/{workspace_id}/members` | [list_workspace_members](../../backend/app/api/workspaces.py) |
| POST | `/workspaces/{workspace_id}/members` | [add_workspace_member](../../backend/app/api/workspaces.py) |
| DELETE | `/workspaces/{workspace_id}/members/{member_user_id}` | [remove_workspace_member](../../backend/app/api/workspaces.py) |
| PATCH | `/workspaces/{workspace_id}/members/{member_user_id}` | [patch_workspace_member](../../backend/app/api/workspaces.py) |
| DELETE | `/workspaces/{workspace_id}/membership` | [leave_workspace](../../backend/app/api/workspaces.py) |
| GET | `/workspaces/{workspace_id}/storage-usage` | [get_workspace_storage_usage](../../backend/app/api/workspaces.py) |
| POST | `/workspaces/{workspace_id}/storage-usage/recalculate` | [recalculate_workspace_storage_usage](../../backend/app/api/workspaces.py) |
| GET | `/workspaces/{workspace_id}/tracks` | [list_workspace_tracks](../../backend/app/api/workspaces.py) |
