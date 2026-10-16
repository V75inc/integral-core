"""Schema and taxonomy materialization for an admitted new Track command."""

from typing import Any

from app.agentive.staging import StagingError
from app.api.validators_common import (
    compute_fold,
    non_empty_after_strip,
    validate_hex_color,
)
from app.services.operational_model_authoring import apply_entry_types_to_track
from app.services.tag_service import create_tag_for_scope


async def apply_track_setup(
    *, user_id: str, workspace_id: str, track_id: str, entry_types: Any, taxonomy: Any
) -> dict[str, Any]:
    """Run inside the canonical creation fence; errors abort the complete setup."""
    result: dict[str, Any] = {}
    if entry_types:
        if not isinstance(entry_types, list):
            raise StagingError("invalid_creation_setup", "Entry types must be a list")
        applied = await apply_entry_types_to_track(
            user_id=user_id, track_id=track_id, entry_types=entry_types
        )
        if applied.get("error"):
            raise StagingError(
                "creation_setup_failed", "Track schema could not be applied"
            )
        result["entry_types_applied"] = applied
    if taxonomy:
        if not isinstance(taxonomy, dict) or not isinstance(
            taxonomy.get("tag_groups", []), list
        ):
            raise StagingError(
                "invalid_creation_setup", "Taxonomy must declare tag groups"
            )
        tags = []
        for group in taxonomy.get("tag_groups", []):
            parents: dict[str, str] = {}
            for spec in group["tags"]:
                name = non_empty_after_strip(spec["name"], "name")
                if len(name) > 80:
                    raise StagingError(
                        "invalid_creation_setup",
                        "Tag names must be 80 characters or fewer",
                    )
                parent = str(spec.get("parent") or "").casefold()
                if parent and parent not in parents:
                    raise StagingError(
                        "invalid_creation_setup",
                        "Taxonomy parent must precede its child",
                    )
                tag = await create_tag_for_scope(
                    user_id,
                    name=name,
                    name_fold=compute_fold(name),
                    color=validate_hex_color(
                        spec.get("color") or "#6B7280", allow_empty=False
                    ),
                    track_id=track_id,
                    group_key=group["key"],
                    parent_tag_id=parents.get(parent),
                    required_workspace_id=workspace_id,
                )
                parents[name.casefold()] = tag.id
                tags.append({"id": tag.id, "name": name, "group_key": group["key"]})
        result["tags_created"] = tags
    return result
