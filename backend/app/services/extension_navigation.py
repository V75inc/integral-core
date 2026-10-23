"""Resolve typed, authorized navigation requests from an App-owned frame."""

from typing import Any
from urllib.parse import quote

from app.api.errors import InsufficientPermissionsError, ResourceNotFoundError
from app.models.nodes import App
from app.services.app_extension_views import _ensure_app_access
from app.services.dashboard_service import get_dashboard
from app.services.permissions import resolve_role
from app.services.resource_location import locate_resource


async def resolve_extension_navigation(
    *, user_id: str, workspace_id: str, app_id: str, kind: str, resource_id: str
) -> dict[str, Any]:
    """Authorize both the frame owner and its destination before returning a path."""
    app = await App.get(app_id)
    if app is None:
        raise ResourceNotFoundError(message="App not found")
    await _ensure_app_access(app, user_id, workspace_id)
    identity = quote(resource_id, safe="")
    if kind == "dashboard":
        dashboard = await get_dashboard(
            user_id=user_id, app_id=app_id, dashboard_id=resource_id
        )
        if dashboard is None:
            raise ResourceNotFoundError(message="Destination unavailable")
        path = (
            f"/apps/{quote(app_id, safe='')}?section=dashboards&dashboard_id={identity}"
        )
    else:
        location = await locate_resource(kind, resource_id)
        if (
            location is None
            or location.workspace_id != workspace_id
            or (resource_id if kind == "app" else location.app_id) != app_id
            or not await resolve_role(user_id, kind, resource_id)
        ):
            raise InsufficientPermissionsError(message="Destination unavailable")
        path = f"/{'entries' if kind == 'entry' else kind + 's'}/{identity}"
    return {"path": path}
