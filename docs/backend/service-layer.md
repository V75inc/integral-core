# Service Layer — Canonical Mutation Entrypoints

Every persisted mutation in Integral routes through a named function in
`backend/app/services/` (or `agentive/services/`). HTTP `@endpoint` handlers,
MCP tools, seed scripts, and agentive staging executors are **thin adapters**
only — they parse input, authorize, and delegate.

Enforced by **I-CRUD-01** (`docs/INVARIANTS.md`) and
`.ci/service_layer_drift_check.sh`.

## Apps

| Operation | Canonical function | Module |
|-----------|-------------------|--------|
| Install library bundle | `install_app` → `app_install` core | `app_lifecycle.py`, `app_install.py` |
| Create blank app (no library) | `create_blank_app_for_user` | `app_service.py` |
| Uninstall | `uninstall_app` | `app_lifecycle.py` |
| Update settings | `update_app_settings` | `app_lifecycle.py` |
| Wire owner + workspace | `wire_app_owner` | `app_graph.py` |

Library installs MUST use `install_app` / `install_bundle_in_workspace` — never
partial merge + provision without seeds/skills/agents.

## Tracks

| Operation | Canonical function | Module |
|-----------|-------------------|--------|
| Create track | `create_track_for_user` | `track_service.py` |
| Attach operational model | `materialize_track_operational_model` | `app_graph.py` |

## Entries

| Operation | Canonical function | Module |
|-----------|-------------------|--------|
| Create / update / delete | `create_entry_internal`, `update_entry_internal`, `delete_entry_internal` | `entry_writer.py` |
| Resolve entry type | `resolve_entry_type_id_for_track` | `entry_type_resolver.py` |

## Operational Models

| Operation | Canonical function | Module |
|-----------|-------------------|--------|
| Merge library manifest | `merge_library_manifest_into_operational_model` | `operational_model_merge.py` |
| Fork / publish / discard draft | `fork_draft`, `publish_draft`, `discard_draft` | `operational_model_service.py` |
| Detach / revert library | `detach_library`, `revert_customizations` | `operational_model_service.py` |

## Entry types

| Operation | Canonical function | Module |
|-----------|-------------------|--------|
| Create entry type (track scope) | `create_entry_type_for_track` | `entry_type_service.py` |

Update/delete remain in `api/entry_types.py` (no `Node.create`/`.connect`);
create drained Wave 2. Remaining profile-side `EntryType.create` sites live
in `api/operational_models.py` (still allowlisted).

## Workspaces & users

| Operation | Canonical function | Module |
|-----------|-------------------|--------|
| Signup graph | `provision_user_graph_on_signup` | `personal_workspace.py` |
| Personal workspace | Created at signup — not lazy on list | `personal_workspace.py` |

## Tags

| Operation | Canonical function | Module |
|-----------|-------------------|--------|
| Create tag (track or app scope) | `create_tag_for_scope` | `tag_service.py` |

Update/delete tag mutations remain in `api/tags.py` for this wave; further
drain tracked on `.ci/service_layer_drift_allowlist.txt`.

## Sharing & notifications

| Operation | Canonical function | Module |
|-----------|-------------------|--------|
| Add collaborator | `add_collaborator` (includes guest membership) | `sharing.py` |
| Redeem share link | `redeem_share_link` | `share_links.py` |
| Create notification | `create_notification` | `app_graph.py` |

## Agentive

| Operation | Canonical function | Module |
|-----------|-------------------|--------|
| Register agent config | `register_agent_config` | `agent_registry_node.py` |
| Register skill | `register_skill` | `skill_registry.py` |

## Allowed direct `Node.create` sites

- Inside the canonical service functions above
- `app_graph.py` bootstrap (`ensure_integral_app_graph`)
- Test fixtures (`tests/conftest.py`)
- One-time scripts (`backend/scripts/`)

All other `backend/app/api/` and `backend/app/agentive/` modules MUST delegate
to services.
