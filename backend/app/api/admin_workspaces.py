"""Platform admin workspace and resource directory endpoints."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Request
from jvspatial.api import endpoint
from pydantic import ValidationError

from app.api.errors import BadRequestError, MissingAuthenticationError
from app.api.utils import export_node, require_platform_admin, resolve_principal_id
from app.schemas.admin import AdminWorkspaceUpdate
from app.services.admin_workspaces import (
    delete_admin_workspace,
    get_admin_app_detail,
    get_admin_track_detail,
    get_admin_workspace_detail,
    list_admin_apps,
    list_admin_tracks,
    list_admin_workspace_members,
    list_admin_workspaces,
    update_admin_workspace,
)
from app.services.change_event import emit_change_event


@endpoint("/admin/workspaces", methods=["GET"], auth=True, tags=["Admin"])
async def admin_list_workspaces(
    request: Request,
    page: int = 1,
    per_page: int = 20,
    search: Optional[str] = None,
    kind: Optional[str] = None,
) -> Dict[str, Any]:
    """Paginated platform-wide workspace directory."""
    require_platform_admin(request)
    result = await list_admin_workspaces(
        page=page,
        per_page=per_page,
        search=search,
        kind=kind,
    )
    return result.model_dump()


@endpoint(
    "/admin/workspaces/{workspace_id}", methods=["GET"], auth=True, tags=["Admin"]
)
async def admin_get_workspace(
    request: Request,
    workspace_id: str,
) -> Dict[str, Any]:
    """Full admin view of a workspace."""
    require_platform_admin(request)
    detail = await get_admin_workspace_detail(workspace_id)
    return {"workspace": detail.model_dump()}


@endpoint(
    "/admin/workspaces/{workspace_id}", methods=["PATCH"], auth=True, tags=["Admin"]
)
async def admin_patch_workspace(
    request: Request,
    workspace_id: str,
) -> Dict[str, Any]:
    """Admin update of workspace metadata."""
    require_platform_admin(request)
    actor_id = resolve_principal_id(request)
    if not actor_id:
        raise MissingAuthenticationError(message="Authentication required")
    try:
        body = AdminWorkspaceUpdate.model_validate(await request.json())
    except ValidationError as e:
        raise BadRequestError(
            message="Validation failed for admin workspace update body",
            details={"errors": e.errors()},
        )
    from app.models.nodes import Workspace

    ws = await Workspace.get(workspace_id)
    if not ws:
        from app.api.errors import ResourceNotFoundError

        raise ResourceNotFoundError(message="Workspace not found")
    before_snapshot = await export_node(ws)
    detail = await update_admin_workspace(workspace_id, body)
    await emit_change_event(
        actor_kind="human",
        actor_id=actor_id,
        action="workspace.update",
        resource_type="Workspace",
        resource_id=workspace_id,
        before=before_snapshot,
        after=detail.model_dump(),
        scope=f"admin:workspace:{workspace_id}",
        details={"admin_action": True},
    )
    return {
        "workspace": detail.model_dump(),
        "message": "Workspace updated successfully",
    }


@endpoint(
    "/admin/workspaces/{workspace_id}", methods=["DELETE"], auth=True, tags=["Admin"]
)
async def admin_delete_workspace(
    request: Request,
    workspace_id: str,
) -> Dict[str, Any]:
    """Cascade-delete a workspace (admin action)."""
    require_platform_admin(request)
    actor_id = resolve_principal_id(request)
    if not actor_id:
        raise MissingAuthenticationError(message="Authentication required")
    from app.models.nodes import Workspace

    ws = await Workspace.get(workspace_id)
    if not ws:
        from app.api.errors import ResourceNotFoundError

        raise ResourceNotFoundError(message="Workspace not found")
    before_snapshot = await export_node(ws)
    await delete_admin_workspace(workspace_id)
    await emit_change_event(
        actor_kind="human",
        actor_id=actor_id,
        action="workspace.delete",
        resource_type="Workspace",
        resource_id=workspace_id,
        before=before_snapshot,
        after=None,
        scope=f"admin:workspace:{workspace_id}",
        details={"admin_action": True},
    )
    return {"message": "Workspace deleted successfully", "workspace_id": workspace_id}


@endpoint(
    "/admin/workspaces/{workspace_id}/members",
    methods=["GET"],
    auth=True,
    tags=["Admin"],
)
async def admin_list_workspace_members(
    request: Request,
    workspace_id: str,
    page: int = 1,
    per_page: int = 50,
    search: Optional[str] = None,
) -> Dict[str, Any]:
    """Paginated workspace member roster (admin view)."""
    require_platform_admin(request)
    result = await list_admin_workspace_members(
        workspace_id,
        page=page,
        per_page=per_page,
        search=search,
    )
    return result.model_dump()


@endpoint("/admin/apps", methods=["GET"], auth=True, tags=["Admin"])
async def admin_list_apps(
    request: Request,
    page: int = 1,
    per_page: int = 20,
    search: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Cross-workspace app directory."""
    require_platform_admin(request)
    result = await list_admin_apps(
        page=page,
        per_page=per_page,
        search=search,
        workspace_id=workspace_id,
    )
    return result.model_dump()


@endpoint("/admin/apps/{app_id}", methods=["GET"], auth=True, tags=["Admin"])
async def admin_get_app(request: Request, app_id: str) -> Dict[str, Any]:
    """Read-only admin view of an app."""
    require_platform_admin(request)
    detail = await get_admin_app_detail(app_id)
    return {"resource": detail.model_dump()}


@endpoint("/admin/tracks/{track_id}", methods=["GET"], auth=True, tags=["Admin"])
async def admin_get_track(request: Request, track_id: str) -> Dict[str, Any]:
    """Read-only admin view of a track."""
    require_platform_admin(request)
    detail = await get_admin_track_detail(track_id)
    return {"resource": detail.model_dump()}


@endpoint("/admin/tracks", methods=["GET"], auth=True, tags=["Admin"])
async def admin_list_tracks(
    request: Request,
    page: int = 1,
    per_page: int = 20,
    search: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Cross-workspace track directory."""
    require_platform_admin(request)
    result = await list_admin_tracks(
        page=page,
        per_page=per_page,
        search=search,
        workspace_id=workspace_id,
    )
    return result.model_dump()
