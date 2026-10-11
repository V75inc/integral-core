"""Current saved-View scope and raw projection, independent of shared models."""

from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError
from app.models.edges import HAS_VIEW
from app.models.nodes import View
from app.services.resource_location import (
    DATA_APP_STATES,
    MANAGEMENT_APP_STATES,
    ResourceLocation,
    locate_resource,
)


async def read_scoped_view(
    view_id: str, *, allowed_app_states: frozenset[str] = DATA_APP_STATES
) -> tuple[View, ResourceLocation] | None:
    """Hydrate only the exact raw View facts that passed canonical admission."""
    records: dict[str, dict] = {}
    location = await locate_resource(
        "view", view_id, allowed_app_states=allowed_app_states, records_out=records
    )
    if location is None:
        return None
    try:
        view = await get_default_context()._deserialize_entity(View, records[view_id])
        if view is None:
            raise AuthorizationUnavailableError()
        return view, location
    except Exception as exc:
        raise AuthorizationUnavailableError() from exc


async def list_scoped_track_views(
    track_id: str, *, allowed_app_states: frozenset[str] = MANAGEMENT_APP_STATES
) -> list[View]:
    """Read bounded scope-edge windows; never select by a scalar Track cache.

    Catalog/default maintenance uses this structural collection too. It must
    not mutate a sibling's configuration merely because a cached Track ID was
    forged or a shared model has several Track attachments.
    """
    context = get_default_context()
    result: list[View] = []
    cursor = ""
    try:
        while True:
            query = {"source": track_id, "entity": HAS_VIEW._entity_name()}
            if cursor:
                query["id"] = {"$gt": cursor}
            edges = await context.database.find(
                "edge", query, limit=128, sort=[("id", 1)]
            )
            seen = {view.id for view in result}
            for edge in edges:
                target = edge.get("target")
                if not isinstance(target, str) or not target:
                    raise AuthorizationUnavailableError()
                located = await read_scoped_view(
                    target, allowed_app_states=allowed_app_states
                )
                if located is not None and located[1].track_id == track_id:
                    if target not in seen:
                        result.append(located[0])
                        seen.add(target)
            if len(edges) < 128:
                return result
            next_cursor = edges[-1].get("id")
            if not isinstance(next_cursor, str) or next_cursor <= cursor:
                raise AuthorizationUnavailableError()
            cursor = next_cursor
    except Exception as exc:
        raise AuthorizationUnavailableError() from exc


async def get_scoped_view(
    view_id: str, *, allowed_app_states: frozenset[str] = DATA_APP_STATES
) -> View | None:
    """Return the current View only after canonical scope admission."""
    located = await read_scoped_view(view_id, allowed_app_states=allowed_app_states)
    return located[0] if located is not None else None


async def get_model_scoped_view(view_id: str, model_id: str) -> View | None:
    """Read a model-only View from an explicitly authorized model catalog.

    This checks exact structural membership; the caller must separately
    authorize its model. It never grants a Track/workspace data capability.
    """
    from app.services.resource_location import ResourceLocationWalker

    context = get_default_context()
    try:
        walker = ResourceLocationWalker(expected_view_model_id=model_id)
        view = await walker.read_node(View, view_id, context)
        if view is None:
            return None
        await walker.spawn(view)
        if any(
            isinstance(report, dict) and "hook_error" in report
            for report in await walker.get_report()
        ):
            raise AuthorizationUnavailableError()
        return view if walker.valid and walker.view_model_matched else None
    except Exception as exc:
        raise AuthorizationUnavailableError() from exc
