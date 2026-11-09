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


async def set_app_track_binding(
    *, user_id: str, app_id: str, track_id: str, present: bool
) -> None:
    """Change an existing Track's parent relationship under workspace admission."""
    from app.api.errors import (
        InsufficientPermissionsError,
        ResourceNotFoundError,
    )
    from app.api.utils import export_node
    from app.models.edges import CONTAINS
    from app.models.nodes import App, Track
    from app.schemas.policy import Resource, Subject
    from app.services.change_event import emit_change_event
    from app.services.membership_write_scope import membership_write_scope
    from app.services.policy_engine import evaluate
    from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource
    from app.utils.time import utc_now_iso

    location = await locate_resource(
        "app", app_id, allowed_app_states=MANAGEMENT_APP_STATES
    )
    if location is None:
        raise ResourceNotFoundError(message="App not found")
    async with membership_write_scope(location.workspace_id, principal_ids=(user_id,)):
        app = await App.find_one({"id": app_id})
        if app is None or app.workspace_id != location.workspace_id:
            raise ResourceNotFoundError(message="App no longer available")
        decision = await evaluate(
            subject=Subject(kind="human", id=user_id),
            action="app.update",
            resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
        )
        if not decision.allowed:
            raise InsufficientPermissionsError(message="Access denied")
        track = await Track.find_one({"id": track_id})
        if track is None:
            raise ResourceNotFoundError(message="Track not found")
        if track.workspace_id != location.workspace_id:
            raise InsufficientPermissionsError(
                message="Track is outside the App workspace"
            )
        before = await export_node(app)
        context = await app.get_context()
        edges = await context.find_edges_between(app.id, track.id, edge_class=CONTAINS)
        if present and not edges:
            await app.connect(track, edge=CONTAINS, added_at=utc_now_iso())
        elif not present:
            for edge in edges:
                await edge.delete()
        after = await export_node(app)

    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="app.update",
        resource_type="App",
        resource_id=app_id,
        before=before,
        after=after,
        scope=f"app:{app_id}",
    )
