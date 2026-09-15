"""App dashboard CRUD and widget data API."""

from __future__ import annotations

import logging
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
    DashboardUpdateRequest,
)
from app.services.change_event import emit_change_event
from app.services.dashboard_service import (
    create_dashboard,
    delete_dashboard,
    get_dashboard,
    list_dashboards,
    resolve_dashboard_data,
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
