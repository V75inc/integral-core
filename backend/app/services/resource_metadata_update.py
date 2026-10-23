"""Canonical App/Track metadata changes with current authority and receipts."""

from contextlib import asynccontextmanager
from typing import Any

from jvspatial.core.context import get_default_context

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.api.utils import export_node
from app.api.validators import (
    normalize_track_accent_color,
    normalize_track_visibility_input,
    validate_app_visibility_workspace,
    validate_track_visibility_workspace,
)
from app.api.validators_common import (
    compute_fold,
    non_empty_after_strip,
    validate_hex_color,
)
from app.models.nodes import App, Track
from app.schemas.policy import Resource, Subject
from app.services.app_operations.transaction_scope import (
    graph_transaction_available,
    postgres_graph_transaction,
)
from app.services.change_event import emit_change_event
from app.services.identity_admission import identity_admission
from app.services.membership_write_scope import membership_write_scope
from app.services.policy_engine import evaluate as policy_evaluate
from app.services.request_scope import require_effect_target_scope
from app.services.resource_events import queue_resource_event as _queue_event
from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource
from app.services.uniqueness import assert_unique
from app.utils.time import utc_now_iso


@asynccontextmanager
async def _metadata_scope(user_id: str, kind: str, resource_id: str, request: Any):
    """Admit the current actor and root before metadata and result commit."""
    from contextlib import AsyncExitStack

    from app.agentive.services.staging_execution import staged_command_effect

    async with AsyncExitStack() as stack:
        native = graph_transaction_available()
        if native:
            await stack.enter_async_context(
                postgres_graph_transaction(get_default_context().database)
            )
        await stack.enter_async_context(identity_admission((user_id,)))
        location = await locate_resource(
            kind, resource_id, allowed_app_states=MANAGEMENT_APP_STATES
        )
        if location is None:
            raise ResourceNotFoundError(message="Resource not found")
        await stack.enter_async_context(
            membership_write_scope(location.workspace_id, principal_ids=(user_id,))
        )
        current = await locate_resource(
            kind, resource_id, allowed_app_states=MANAGEMENT_APP_STATES
        )
        if current is None or current.workspace_id != location.workspace_id:
            raise ResourceNotFoundError(message="Resource scope changed")
        await require_effect_target_scope(request, user_id, current.workspace_id)
        decision = await policy_evaluate(
            subject=Subject(kind="human", id=user_id),
            action=f"{kind}.update",
            resource=Resource(kind=kind, id=resource_id, scope=f"{kind}:{resource_id}"),
        )
        if not decision.allowed:
            raise InsufficientPermissionsError(message="Access denied")
        complete = await stack.enter_async_context(
            staged_command_effect(
                user_id=user_id,
                workspace_id=current.workspace_id,
                kind=f"update_{kind}",
                targets={f"{kind}_id": resource_id},
            )
        )
        yield complete, current.workspace_id, native


async def update_app_metadata(
    *,
    user_id: str,
    app_id: str,
    request: Any = None,
    name: str | None = None,
    description: str | None = None,
    visibility: str | None = None,
    accent_color: str | None = None,
    icon: str | None = None,
    home: dict | None = None,
    expected_home_revision: str | None = None,
) -> dict:
    """Update generic App metadata; never move its workspace or bypass Home checks."""
    async with _metadata_scope(user_id, "app", app_id, request) as (
        complete,
        workspace_id,
        native,
    ):
        sp = await App.get(app_id)
        if sp is None:
            raise ResourceNotFoundError(message="App not found")
        # The workspace write fence serializes Home authors. Read the primary
        # record inside that fence rather than trusting an earlier graph cache.
        if home is not None:
            from app.services.authority_facts import primary_node_record

            graph = get_default_context()
            row = await primary_node_record(graph.database, App, app_id)
            if row is None:
                raise ResourceNotFoundError(message="App not found")
            sp = await graph._deserialize_entity(App, row)
            from app.services.app_home import home_revision_token

            revision = home_revision_token(sp)
            if expected_home_revision is not None and (
                isinstance(expected_home_revision, bool)
                or expected_home_revision != revision
            ):
                raise ResourceConflictError(
                    message="Home changed. Read the current Home before applying this edit."
                )
            current_home = sp.home_composition or {}

            def custom(value):
                return value.get("renderer") in {"native", "extension"} or any(
                    widget.get("type") in {"native_component", "extension_view"}
                    for widget in value.get("widgets", [])
                )

            if expected_home_revision is None and (
                custom(home) or custom(current_home)
            ):
                raise BadRequestError(
                    message="Custom Home edits require expected_home_revision from the Home readback"
                )
        normalized_home = None
        if home is not None:
            from app.services.app_home_override import validate_home_composition

            try:
                normalized_home = await validate_home_composition(
                    user_id=user_id, app_id=app_id, home=home
                )
            except ValueError as exc:
                raise BadRequestError(message=str(exc)) from exc
        prior_snapshot = await export_node(sp)  # D-03 before-snapshot

        if normalized_home is not None:
            sp.home_composition = normalized_home or None
            sp.home_revision = int(getattr(sp, "home_revision", 0) or 0) + 1
        if name is not None:
            safe_name = non_empty_after_strip(name, "name")
            if len(safe_name) > 200:
                raise BadRequestError(message="name must be 200 characters or fewer")
            new_fold = compute_fold(safe_name)
            if new_fold != (getattr(sp, "name_fold", "") or ""):
                await assert_unique(
                    App,
                    {
                        "context.workspace_id": sp.workspace_id,
                        "context.name_fold": new_fold,
                    },
                    entity="app",
                    field_label="name",
                    value=safe_name,
                    scope_label="in this workspace",
                    exclude_id=sp.id,
                )
            sp.name = safe_name
            sp.name_fold = new_fold
        if description is not None:
            sp.description = description
        if icon is not None:
            from app.services.app_identity import validate_app_icon

            sp.icon = validate_app_icon(icon)
        if accent_color is not None:
            sp.accent_color = validate_hex_color(accent_color, allow_empty=True)
        if visibility is not None:
            v = visibility.strip()
            if v not in ("private", "workspace", "public"):
                raise BadRequestError(
                    message="visibility must be private, workspace, or public",
                )
            sp.visibility = v
        vis = getattr(sp, "visibility", None) or "private"
        await validate_app_visibility_workspace(vis, sp.workspace_id)
        sp.updated_at = utc_now_iso()
        await sp.save()

        event = {
            "actor_kind": "human",
            "actor_id": user_id,
            "action": "app.update",
            "resource_type": "App",
            "resource_id": sp.id,
            "before": prior_snapshot,
            "after": await export_node(sp),
            "scope": f"app:{sp.id}",
        }
        outbox = await _queue_event(event, workspace_id, native)
        if complete is not None:
            await complete({"app_id": sp.id})
    await sp.set_context(get_default_context())
    if not native:
        await emit_change_event(**event)
    else:
        from app.agentive.services.staging_execution import (
            deliver_staging_command_events,
        )
        from app.services.workspace_membership import _deliver

        await _deliver(outbox)
        await deliver_staging_command_events()
    return {"app": await export_node(sp), "message": "App updated successfully"}


async def update_track_metadata(
    *,
    user_id: str,
    track_id: str,
    request: Any = None,
    title: str | None = None,
    purpose: str | None = None,
    icon: str | None = None,
    visibility: str | None = None,
    accent_color: str | None = None,
) -> dict:
    """Update generic Track metadata while preserving declared relation identity."""
    async with _metadata_scope(user_id, "track", track_id, request) as (
        complete,
        workspace_id,
        native,
    ):
        track = await Track.get(track_id)
        if track is None:
            raise ResourceNotFoundError(message="Track not found")
        prior_snapshot = await export_node(track)  # D-03 before-snapshot

        if title is not None:
            safe_title = non_empty_after_strip(title, "title")
            if len(safe_title) > 200:
                raise BadRequestError(message="title must be 200 characters or fewer")
            new_fold = compute_fold(safe_title)
            if new_fold != (getattr(track, "title_fold", "") or ""):
                await assert_unique(
                    Track,
                    {
                        "context.workspace_id": track.workspace_id,
                        "context.title_fold": new_fold,
                    },
                    entity="track",
                    field_label="title",
                    value=safe_title,
                    scope_label="in this workspace",
                    exclude_id=track.id,
                )
            if not track.template_id and not track.relation_type_key:
                from app.services.track_relation_identity import relation_track_type

                track.relation_type_key = await relation_track_type(track)
            track.title = safe_title
            track.title_fold = new_fold
        if purpose is not None:
            track.purpose = purpose
        if icon is not None:
            track.icon = icon
        if visibility is not None:
            explicit_vis = normalize_track_visibility_input(visibility)
            if explicit_vis is None:
                track.visibility = "private"
            else:
                await validate_track_visibility_workspace(
                    explicit_vis, track.workspace_id
                )
                track.visibility = explicit_vis
        if accent_color is not None:
            track.accent_color = normalize_track_accent_color(accent_color)

        track.updated_at = utc_now_iso()
        await track.save()

        event = {
            "actor_kind": "human",
            "actor_id": user_id,
            "action": "track.update",
            "resource_type": "Track",
            "resource_id": track.id,
            "before": prior_snapshot,
            "after": await export_node(track),
            "scope": f"track:{track.id}",
        }
        outbox = await _queue_event(event, workspace_id, native)
        if complete is not None:
            await complete({"track_id": track.id})
    await track.set_context(get_default_context())
    if not native:
        await emit_change_event(**event)
    else:
        from app.agentive.services.staging_execution import (
            deliver_staging_command_events,
        )
        from app.services.workspace_membership import _deliver

        await _deliver(outbox)
        await deliver_staging_command_events()
    return {"track": await export_node(track), "message": "Track updated successfully"}
