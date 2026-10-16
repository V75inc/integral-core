"""Fresh Dashboard projection after canonical named-containment admission."""

from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError
from app.models.nodes import Dashboard
from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource


async def read_scoped_dashboard(app_id: str, dashboard_id: str) -> Dashboard | None:
    """Read only a Dashboard that still belongs to exactly the expected App."""
    records: dict[str, dict] = {}
    location = await locate_resource(
        "dashboard",
        dashboard_id,
        allowed_app_states=MANAGEMENT_APP_STATES,
        records_out=records,
    )
    if location is None or location.app_id != app_id:
        return None
    try:
        dashboard = await get_default_context()._deserialize_entity(
            Dashboard, records[dashboard_id]
        )
        if dashboard is None:
            raise AuthorizationUnavailableError()
        return dashboard
    except Exception as exc:
        raise AuthorizationUnavailableError() from exc


async def list_scoped_app_dashboards(app_id: str) -> list[Dashboard]:
    """Use scalar App attribution only to select bounded candidate windows."""
    database = get_default_context().database
    result = []
    cursor = ""
    while True:
        query: dict = {"entity": Dashboard._entity_name(), "context.app_id": app_id}
        if cursor:
            query["id"] = {"$gt": cursor}
        try:
            rows = await database.find("node", query, limit=128, sort=[("id", 1)])
        except Exception as exc:
            raise AuthorizationUnavailableError() from exc
        for row in rows:
            dashboard_id = row.get("id")
            if not isinstance(dashboard_id, str) or not dashboard_id:
                raise AuthorizationUnavailableError()
            dashboard = await read_scoped_dashboard(app_id, dashboard_id)
            if dashboard is not None:
                result.append(dashboard)
        if len(rows) < 128:
            return result
        next_cursor = rows[-1]["id"]
        if next_cursor <= cursor:
            raise AuthorizationUnavailableError()
        cursor = next_cursor
