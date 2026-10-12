"""Approved and human App creation shares the canonical rooted producer."""

from typing import Any

from app.api.errors import BadRequestError, InsufficientPermissionsError
from app.schemas.policy import Resource, Subject


async def create_app_internal(
    *,
    actor_kind: str,
    actor_id: str,
    payload: dict[str, Any],
    _internal_actor: Subject | None = None,
) -> dict:
    """Create under current delegator authority with original actor attribution."""
    from app.services.app_service import create_app_for_user
    from app.services.approved_effect import (
        approved_effect_principal,
        approved_event_sink,
    )
    from app.services.membership_write_scope import membership_write_scope
    from app.services.permissions import get_user_node
    from app.services.policy_engine import evaluate

    allowed = {
        "title",
        "name",
        "workspace_id",
        "description",
        "visibility",
        "library_operational_model_id",
        "accent_color",
        "include_seed_data",
    }
    if set(payload) - allowed or (
        "title" in payload and "name" in payload and payload["title"] != payload["name"]
    ):
        raise BadRequestError(message="Unsupported or conflicting approved App fields")
    workspace_id = payload.get("workspace_id")
    if not isinstance(workspace_id, str) or not workspace_id:
        raise BadRequestError(message="Missing workspace_id")
    principal = await approved_effect_principal(
        actor_kind=actor_kind,
        actor_id=actor_id,
        action="app.create",
        resource_type="workspace",
        resource_id=workspace_id,
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
            action="app.create",
            resource=Resource(kind="app", id="", scope=f"workspace:{workspace_id}"),
            payload=payload,
        )
        if not decision.allowed:
            raise InsufficientPermissionsError(message="App creation denied")
        app = await create_app_for_user(
            user_id=principal,
            name=payload.get("name", payload.get("title", "Untitled App")),
            description=payload.get("description"),
            workspace_id=workspace_id,
            visibility=payload.get("visibility"),
            library_package_id=payload.get("library_operational_model_id"),
            accent_color=payload.get("accent_color"),
            include_seed_data=payload.get("include_seed_data", True),
            actor_kind=actor_kind,
            actor_id=actor_id,
            change_event_sink=approved_event_sink if actor_kind != "human" else None,
        )
        return await app.export(flat=True)
