"""App dashboard CRUD and widget data API."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    MissingAuthenticationError,
    ResourceNotFoundError,
)
from app.api.utils import export_node, resolve_principal_id
from app.models.nodes import App
from app.schemas.dashboards import (
    DashboardCreateRequest,
    DashboardDrilldownRequest,
    DashboardUpdateRequest,
)
from app.schemas.query_spec import QuerySpecResult
from app.services.change_event import emit_change_event
from app.services.dashboard_service import (
    build_dashboard_drilldown_spec,
    create_dashboard,
    delete_dashboard,
    get_dashboard,
    list_dashboards,
    resolve_dashboard_data,
    resolve_widget_data,
    suggest_dashboard_template,
    update_dashboard,
)
from app.services.request_scope import resolve_workspace_id_from_request
from app.views import dashboard_widget_types as dwt

logger = logging.getLogger(__name__)


async def _require_app(app_id: str) -> App:
    app = await App.get(app_id)
    if not app:
        raise ResourceNotFoundError(message="App not found", details={"app_id": app_id})
    return app


@endpoint("/apps/{app_id}/dashboards", methods=["GET"], auth=True, tags=["Dashboards"])
async def list_app_dashboards(request: Request, app_id: str) -> Dict[str, Any]:
    """List dashboards cataloged under an App."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    await _require_app(app_id)
    boards = await list_dashboards(user_id=user_id, app_id=app_id)
    exported = [await export_node(d) for d in boards]
    return {"dashboards": exported, "total": len(exported)}


@endpoint("/apps/{app_id}/dashboards", methods=["POST"], auth=True, tags=["Dashboards"])
async def create_app_dashboard(request: Request, app_id: str) -> Dict[str, Any]:
    """Create a dashboard under an App."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    await _require_app(app_id)
    try:
        raw = await request.json()
    except Exception as exc:
        raise BadRequestError(message=f"Invalid JSON body: {exc}") from exc
    if not isinstance(raw, dict):
        raise BadRequestError(message="Request body must be a JSON object.")
    body = DashboardCreateRequest.model_validate(raw)
    try:
        dash = await create_dashboard(
            user_id=user_id,
            app_id=app_id,
            name=body.name,
            layout=body.layout.model_dump() if body.layout else None,
            widgets=[w.model_dump() for w in body.widgets] if body.widgets else None,
            is_default=body.is_default,
        )
    except PermissionError:
        raise InsufficientPermissionsError(message="Access denied")
    except ValueError as exc:
        raise BadRequestError(message=str(exc))
    exported = await export_node(dash)
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="dashboard.create",
        resource_type="Dashboard",
        resource_id=dash.id,
        before=None,
        after=exported,
        scope=f"app:{app_id}",
    )
    return exported


@endpoint(
    "/apps/{app_id}/dashboards/suggest",
    methods=["GET"],
    auth=True,
    tags=["Dashboards"],
)
async def suggest_app_dashboard(request: Request, app_id: str) -> Dict[str, Any]:
    """Return a heuristic dashboard template for an App."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    return await suggest_dashboard_template(
        user_id=user_id, app_id=app_id, workspace_id=workspace_id
    )


@endpoint(
    "/apps/{app_id}/dashboards/{dashboard_id}",
    methods=["GET"],
    auth=True,
    tags=["Dashboards"],
)
async def get_app_dashboard(
    request: Request, app_id: str, dashboard_id: str
) -> Dict[str, Any]:
    """Fetch one dashboard by id."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    dash = await get_dashboard(
        user_id=user_id, app_id=app_id, dashboard_id=dashboard_id
    )
    if not dash:
        raise ResourceNotFoundError(message="Dashboard not found")
    return await export_node(dash)


@endpoint(
    "/apps/{app_id}/dashboards/{dashboard_id}",
    methods=["PATCH"],
    auth=True,
    tags=["Dashboards"],
)
async def patch_app_dashboard(
    request: Request,
    app_id: str,
    dashboard_id: str,
) -> Dict[str, Any]:
    """Update dashboard metadata, layout, or widgets."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    try:
        raw = await request.json()
    except Exception as exc:
        raise BadRequestError(message=f"Invalid JSON body: {exc}") from exc
    if not isinstance(raw, dict):
        raise BadRequestError(message="Request body must be a JSON object.")
    body = DashboardUpdateRequest.model_validate(raw)
    prior = await get_dashboard(
        user_id=user_id, app_id=app_id, dashboard_id=dashboard_id
    )
    if not prior:
        raise ResourceNotFoundError(message="Dashboard not found")
    prior_snapshot = await export_node(prior)
    try:
        dash = await update_dashboard(
            user_id=user_id,
            app_id=app_id,
            dashboard_id=dashboard_id,
            name=body.name,
            layout=body.layout.model_dump() if body.layout else None,
            widgets=[w.model_dump() for w in body.widgets] if body.widgets else None,
            is_default=body.is_default,
        )
    except PermissionError:
        raise InsufficientPermissionsError(message="Access denied")
    except ValueError as exc:
        raise ResourceNotFoundError(message=str(exc))
    exported = await export_node(dash)
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="dashboard.update",
        resource_type="Dashboard",
        resource_id=dash.id,
        before=prior_snapshot,
        after=exported,
        scope=f"app:{app_id}",
    )
    return exported


@endpoint(
    "/apps/{app_id}/dashboards/{dashboard_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Dashboards"],
)
async def delete_app_dashboard(
    request: Request, app_id: str, dashboard_id: str
) -> Dict[str, Any]:
    """Delete a dashboard from an App."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    prior = await get_dashboard(
        user_id=user_id, app_id=app_id, dashboard_id=dashboard_id
    )
    if not prior:
        raise ResourceNotFoundError(message="Dashboard not found")
    prior_snapshot = await export_node(prior)
    try:
        ok = await delete_dashboard(
            user_id=user_id, app_id=app_id, dashboard_id=dashboard_id
        )
    except PermissionError:
        raise InsufficientPermissionsError(message="Access denied")
    if not ok:
        raise ResourceNotFoundError(message="Dashboard not found")
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="dashboard.delete",
        resource_type="Dashboard",
        resource_id=dashboard_id,
        before=prior_snapshot,
        after=None,
        scope=f"app:{app_id}",
    )
    return {"deleted": True, "dashboard_id": dashboard_id}


@endpoint(
    "/apps/{app_id}/dashboards/{dashboard_id}/data",
    methods=["GET"],
    auth=True,
    tags=["Dashboards"],
)
async def get_app_dashboard_data(
    request: Request, app_id: str, dashboard_id: str
) -> Dict[str, Any]:
    """Resolve live data for every widget on a dashboard."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    dash = await get_dashboard(
        user_id=user_id, app_id=app_id, dashboard_id=dashboard_id
    )
    if not dash:
        raise ResourceNotFoundError(message="Dashboard not found")
    widget_data = await resolve_dashboard_data(
        user_id=user_id,
        app_id=app_id,
        dashboard=dash,
        workspace_id=workspace_id,
    )
    return {"widget_data": widget_data}


@endpoint(
    "/apps/{app_id}/dashboards/{dashboard_id}/drill-through",
    methods=["POST"],
    auth=True,
    tags=["Dashboards"],
)
async def get_dashboard_widget_result_set(
    request: Request, app_id: str, dashboard_id: str
) -> Dict[str, Any]:
    """Open the saved widget scope as a broker-governed fixed result set."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    if not workspace_id:
        raise BadRequestError(message="A workspace scope is required for drill-through")
    dash = await get_dashboard(
        user_id=user_id, app_id=app_id, dashboard_id=dashboard_id
    )
    if not dash:
        raise ResourceNotFoundError(message="Dashboard not found")
    try:
        body = DashboardDrilldownRequest.model_validate(await request.json())
    except Exception as exc:  # noqa: BLE001 — canonical request envelope
        raise BadRequestError(
            message="Invalid dashboard drill-through request"
        ) from exc
    widget = next(
        (row for row in dash.widgets or [] if row.get("id") == body.widget_id), None
    )
    if widget is None:
        raise ResourceNotFoundError(message="Dashboard widget not found")
    app = await _require_app(app_id)
    from app.services.query_boundary import decide_app

    decision = decide_app(app)
    if not decision.allowed:
        raise InsufficientPermissionsError(
            message="This App requires a declared query capability",
            details=decision.public(app_id=app_id),
        )
    try:
        spec = await build_dashboard_drilldown_spec(
            app=app,
            widget=widget,
            group_key=body.group_key,
            cursor=body.cursor,
        )
    except ValueError as exc:
        raise BadRequestError(message=str(exc)) from exc

    from app.agentive.services import capability_broker

    result = await capability_broker.invoke_declared_capability(
        principal_id=user_id,
        workspace_id=workspace_id,
        capability_key="integral_query_spec",
        origin="http",
        source="core",
        op_class="read",
        arguments={
            "spec": spec.model_dump(mode="json", exclude_none=True),
            **({"result_set_id": body.result_set_id} if body.result_set_id else {}),
        },
    )
    if not result.ok:
        raise BadRequestError(
            message=result.message or "Dashboard drill-through failed",
            details={"error_code": result.error_code},
        )
    payload = QuerySpecResult.model_validate(result.data or {}).model_dump(mode="json")
    payload["membership_scope"] = {
        "app_id": app_id,
        "dashboard_id": dashboard_id,
        "widget_id": body.widget_id,
        "group_key": body.group_key,
    }
    payload["calculation"] = {
        "op": (widget.get("data_source") or {}).get("op", "count"),
        "field": (widget.get("data_source") or {}).get("field") or None,
        "group_by": (widget.get("data_source") or {}).get("group_by") or None,
    }
    payload["membership_limit"] = spec.limit
    payload["total_estimate"] = payload.get("total_estimate")
    payload["membership_complete"] = not bool(payload.get("next_cursor"))
    payload["continuation_contract"] = "cursor_pages_revalidated_under_current_access"
    payload["refreshed_at"] = datetime.now(timezone.utc).isoformat()
    track_filters = [item for item in spec.filters if item.field != "track_id"]
    track_ids = [
        str(item.value)
        for item in spec.filters
        if item.field == "track_id" and item.op == "eq" and item.value
    ]
    if len(track_ids) == 1:
        payload["track_navigation"] = {
            "track_id": track_ids[0],
            "filters": [
                item.model_dump(mode="json", exclude_none=True)
                for item in track_filters
            ],
        }
    source = dict(widget.get("data_source") or {})
    items = list(payload.get("items") or [])
    aggregate_rows_for_page: list[Dict[str, Any]] = []
    for item in items:
        custom_fields: Dict[str, Any] = {}
        row = dict(item)
        for key in list(row):
            if key.startswith("custom_fields."):
                custom_fields[key.split(".", 1)[1]] = row.pop(key)
        row["custom_fields"] = custom_fields
        aggregate_rows_for_page.append(row)
    from app.schemas.entry_aggregate import AggregateSpec
    from app.services.entry_aggregate import aggregate_rows

    page_spec = AggregateSpec(
        op=source.get("op") or "count",
        field=source.get("field") or "",
        timezone=source.get("timezone") or "UTC",
        budget=max(1, len(aggregate_rows_for_page)),
    )
    payload["page_calculation"] = aggregate_rows(aggregate_rows_for_page, page_spec)
    payload["page_truncated"] = bool(payload.get("next_cursor"))
    widget_data = await resolve_widget_data(
        user_id=user_id,
        app_id=app_id,
        widget=widget,
        workspace_id=workspace_id,
    )
    payload["current_widget_value"] = (
        widget_data.get("display")
        if widget_data.get("display") is not None
        else widget_data.get("value")
    )
    if payload["current_widget_value"] is None and body.group_key is not None:
        # Grouped chart resolvers expose their values as series rather than a
        # scalar. Report the selected bar's live value so the drill-through
        # page calculation can be compared to the exact plotted group.
        series = widget_data.get("series") or []
        matching_group = next(
            (
                row
                for row in series
                if isinstance(row, dict)
                and str(row.get("label") or "") == str(body.group_key)
            ),
            None,
        )
        if matching_group is not None:
            payload["current_widget_value"] = matching_group.get("value")
    if widget_data.get("error"):
        payload["current_widget_error"] = widget_data["error"]
    if widget_data.get("refused"):
        payload["current_widget_refused"] = widget_data["refused"]
    return payload


@endpoint(
    "/dashboard-widget-substrate",
    methods=["GET"],
    auth=True,
    tags=["Dashboards"],
)
async def get_dashboard_widget_substrate(request: Request) -> Dict[str, Any]:
    """Return the dashboard widget palette for agents and the UI."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    def _payload(spec: Any) -> Dict[str, Any]:
        return {
            "type": spec.type,
            "label": spec.label,
            "description": spec.description,
            "config_schema": spec.config_schema,
            "data_source_schema": spec.data_source_schema,
            "source": spec.source,
            "palette_group": spec.palette_group,
            "configurable": spec.configurable,
            "default_size": dict(spec.default_size),
        }

    return {
        "widget_types": [_payload(s) for s in dwt.iter_specs()],
        "registry_version": dwt.registry_version(),
    }
