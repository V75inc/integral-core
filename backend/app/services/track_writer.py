"""Approved and human Track creation shares the canonical rooted producer."""

from typing import Any

from app.api.errors import BadRequestError, InsufficientPermissionsError
from app.schemas.policy import Resource, Subject


async def create_track_internal(
    *,
    actor_kind: str,
    actor_id: str,
    payload: dict[str, Any],
    _internal_actor: Subject | None = None,
) -> dict:
    """Keep human ownership and original actor audit under exact replay proof."""
    from app.services.approved_effect import (
        approved_effect_principal,
        approved_event_sink,
    )
    from app.services.membership_write_scope import membership_write_scope
    from app.services.permissions import get_user_node
    from app.services.policy_engine import evaluate
    from app.services.track_service import create_track_in_space

    allowed = {
        "title",
        "workspace_id",
        "purpose",
        "icon",
        "visibility",
        "template_id",
        "app_id",
        "library_operational_model_id",
        "app_track_template_operational_model_id",
        "app_track_type_key",
        "accent_color",
    }
    if set(payload) - allowed:
        raise BadRequestError(message="Unsupported approved Track fields")
    workspace_id = payload.get("workspace_id")
    if not isinstance(workspace_id, str) or not workspace_id:
        raise BadRequestError(message="Missing workspace_id")
    parent_kind, parent_id = (
        ("app", payload["app_id"])
        if payload.get("app_id")
        else ("workspace", workspace_id)
    )
    principal = await approved_effect_principal(
        actor_kind=actor_kind,
        actor_id=actor_id,
        action="track.create",
        resource_type=parent_kind,
        resource_id=parent_id,
        payload=payload,
    )
    if principal is None:
        if actor_kind != "human":
            raise InsufficientPermissionsError(
                message="Exact approved authority required"
            )
        user = await get_user_node(actor_id)
        principal = user.id if user else ""
    async with membership_write_scope(workspace_id, principal_ids=(principal,)):
        decision = await evaluate(
            subject=Subject(kind=actor_kind, id=actor_id),
            action="track.create",
            resource=Resource(kind="track", id="", scope=f"workspace:{workspace_id}"),
            payload=payload,
        )
        if not decision.allowed:
            raise InsufficientPermissionsError(message="Track creation denied")
        fields = {
            key: value
            for key, value in payload.items()
            if key not in {"title", "workspace_id"}
        }
        track = await create_track_in_space(
            user_id=principal,
            title=payload.get("title", "Untitled Track"),
            workspace_id=workspace_id,
            actor_kind=actor_kind,
            actor_id=actor_id,
            change_event_sink=approved_event_sink if actor_kind != "human" else None,
            **fields,
        )
        return await track.export(flat=True)
