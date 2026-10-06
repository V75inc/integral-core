"""Effect classification for staged writes.

This is a policy classification of resolved operation kinds, not a parser of
user text. Unknown kinds receive the material-review class and never inherit
low-risk behavior by default.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping


class ApprovalEffectClass(str, Enum):
    PRIVATE_REVERSIBLE = "private_reversible"
    MATERIAL_EXTERNAL = "material_external"
    DESTRUCTIVE_SECURITY = "destructive_security"


POLICY_VERSION = "integral-staged-write-v1"

_DESTRUCTIVE_SECURITY_KINDS = frozenset(
    {
        "delete_app",
        "delete_track",
        "delete_entry",
        "bulk_delete_entries",
        "delete_comment",
        "delete_view",
        "delete_dashboard",
        "delete_skill",
        "delete_routine",
        "routine_task_purge",
        "remove_collaborator",
        "set_exclusion",
        "revoke_share_link",
        "publish_profile_draft",
        "apply_library_operational_model",
        "migrate_track",
        "migrate_app",
        "migrate_operational_model",
    }
)

_MATERIAL_EXTERNAL_KINDS = frozenset(
    {
        "share",
        "invite",
        "mint_share_link",
        "mcp_tool_call",
        "trigger_sync",
        "resolve_conflict",
        "onboard_user",
        "connector_sync",
        "batch",
        "bulk_update_entries",
        "bulk_move_entries",
        "merge_tracks",
        "merge_tags",
        "schedule_task",
        "routine_task_create",
        "routine_task_update",
        "routine_task_cancel",
        "author_operational_model",
        "modify_operational_model",
    }
)


def effect_class(
    kind: str, payload: Mapping[str, Any] | None = None
) -> ApprovalEffectClass:
    """Classify a staged operation using its governed kind and batch contents."""
    normalized = str(kind or "").strip().lower()
    data = payload or {}
    if normalized == "batch":
        nested = data.get("ops")
        if isinstance(nested, list):
            classes = [
                effect_class(str(op.get("kind") or ""), op)
                for op in nested
                if isinstance(op, Mapping)
            ]
            if ApprovalEffectClass.DESTRUCTIVE_SECURITY in classes:
                return ApprovalEffectClass.DESTRUCTIVE_SECURITY
            if ApprovalEffectClass.MATERIAL_EXTERNAL in classes:
                return ApprovalEffectClass.MATERIAL_EXTERNAL
            if classes:
                return ApprovalEffectClass.PRIVATE_REVERSIBLE
    if normalized in _DESTRUCTIVE_SECURITY_KINDS:
        return ApprovalEffectClass.DESTRUCTIVE_SECURITY
    if normalized in _MATERIAL_EXTERNAL_KINDS:
        return ApprovalEffectClass.MATERIAL_EXTERNAL
    # Fail conservatively when a new operation kind has not been classified.
    if normalized not in {
        "create_entry",
        "update_entry",
        "file_content",
        "attach_file",
        "attach_uploaded_file",
        "attach_uploaded_image",
        "transform_entry",
        "bulk_update_entries",
        "add_entry_tag",
        "remove_entry_tag",
        "create_tag",
        "update_tag",
        "link_entries",
        "add_comment",
        "edit_comment",
        "save_view",
        "create_dashboard",
        "update_dashboard",
        "create_track",
        "create_app",
        "update_track",
        "update_app",
        "update_skill",
        "propose_profile_revision",
        "discard_profile_draft",
        "create_app_track",
        "routine_task_create",
        "routine_task_update",
        "remove_exclusion",
        "register_track_template",
    }:
        return ApprovalEffectClass.MATERIAL_EXTERNAL
    return ApprovalEffectClass.PRIVATE_REVERSIBLE
