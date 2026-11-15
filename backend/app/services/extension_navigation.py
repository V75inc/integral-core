"""Resolve typed, authorized navigation requests from an App-owned frame."""

from typing import Any
from urllib.parse import quote

from app.api.errors import InsufficientPermissionsError, ResourceNotFoundError
from app.models.nodes import App, Entry, EntryType
from app.services.app_extension_views import _ensure_app_access
from app.services.dashboard_service import get_dashboard
from app.services.permissions import resolve_role
from app.services.resource_location import locate_resource


async def resolve_extension_navigation(
    *,
    user_id: str,
    workspace_id: str,
    app_id: str,
    kind: str,
    resource_id: str,
    source_entry_id: str | None = None,
    field_key: str | None = None,
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
            or not await resolve_role(user_id, kind, resource_id)
        ):
            raise InsufficientPermissionsError(message="Destination unavailable")
        owner = resource_id if kind == "app" else location.app_id
        if owner != app_id:
            if (
                kind != "entry"
                or not source_entry_id
                or not field_key
                or not await _related_destination(
                    user_id,
                    workspace_id,
                    app_id,
                    source_entry_id,
                    field_key,
                    resource_id,
                    owner,
                )
            ):
                raise InsufficientPermissionsError(message="Destination unavailable")
        path = f"/{'entries' if kind == 'entry' else kind + 's'}/{identity}"
    return {"path": path}


async def _related_destination(
    user_id, workspace_id, app_id, source_id, field_key, target_id, target_app_id
):
    """A persisted typed cross-App reference grants navigation, never data access."""
    source_location = await locate_resource("entry", source_id)
    if (
        source_location is None
        or source_location.workspace_id != workspace_id
        or source_location.app_id != app_id
        or not await resolve_role(user_id, "entry", source_id)
    ):
        return False
    source = await Entry.get(source_id)
    target = await Entry.get(target_id)
    if source is None or target is None:
        return False
    source_type = await EntryType.get(source.type_id)
    target_type = await EntryType.get(target.type_id)
    if source_type is None or target_type is None:
        return False
    spec = next(
        (
            field
            for field in source_type.form_schema.get("fields", [])
            if field.get("key") == field_key and field.get("type") == "relation"
        ),
        {},
    )
    relation = spec.get("relation") or {}
    if not relation.get("allow_cross_app") or not relation.get("target_app"):
        return False
    value = (source.custom_fields or {}).get(field_key)

    def references(value):
        if isinstance(value, list):
            return any(references(item) for item in value)
        if isinstance(value, dict):
            return (value.get("id") or value.get("entry_id")) == target_id
        return value == target_id

    if not references(value):
        return False
    allowed_types = relation.get("target_entry_types") or []
    if (
        allowed_types
        and target_type.form_schema.get("_manifest_entry_type_key") not in allowed_types
    ):
        return False
    target_app = await App.get(target_app_id)
    if (
        target_app is None
        or target_app.installed_package_slug != relation["target_app"]
    ):
        return False
    await _ensure_app_access(target_app, user_id, workspace_id)
    return True
