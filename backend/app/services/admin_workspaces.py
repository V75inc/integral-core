"""Platform admin workspace and resource directory services."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from jvspatial.api.auth.models import User as AuthUser
from jvspatial.core.pager import ObjectPager

from app.models.edges import IS_MEMBER_OF
from app.models.nodes import App, Track, User, Workspace
from app.schemas.admin import (
    AdminMemberListItem,
    AdminMemberListResponse,
    AdminOwnerSummary,
    AdminResourceDetail,
    AdminResourceListItem,
    AdminResourceListResponse,
    AdminWorkspaceDetail,
    AdminWorkspaceListItem,
    AdminWorkspaceListResponse,
    AdminWorkspaceUpdate,
)
from app.services.workspace_lifecycle import delete_workspace_cascade
from app.services.workspace_permissions import get_workspace_owner_user_id


def _resource_label(node: Any, *, kind: str) -> str:
    if kind == "track":
        return getattr(node, "title", "") or ""
    return getattr(node, "name", "") or ""


async def _workspace_counts(ws: Workspace) -> tuple[int, int, int]:
    member_count = 0
    app_count = 0
    track_count = 0
    try:
        members = await ws.nodes(edge=[IS_MEMBER_OF], direction="in", node=["User"])
        member_count = len(members)
    except Exception:
        pass
    try:
        apps = await ws.nodes(edge=["CONTAINS"], direction="out", node=["WorkspaceApp"])
        app_count = len(apps)
    except Exception:
        pass
    try:
        tracks = await ws.nodes(edge=["CONTAINS"], direction="out", node=["Track"])
        track_count = len(tracks)
    except Exception:
        pass
    return member_count, app_count, track_count


async def _owner_summaries_for_ids(
    user_node_ids: List[Optional[str]],
) -> Dict[str, AdminOwnerSummary]:
    """Batch-resolve User node ids to owner summaries."""
    unique: List[str] = []
    seen: set[str] = set()
    for uid in user_node_ids:
        if uid and uid not in seen:
            seen.add(uid)
            unique.append(uid)
    out: Dict[str, AdminOwnerSummary] = {}
    for uid in unique:
        user = await User.get(uid)
        if not user:
            continue
        email = await _auth_email(user)
        out[uid] = AdminOwnerSummary(
            id=user.id,
            display_name=user.display_name or "",
            email=email,
        )
    return out


async def _workspace_owner_summary(ws: Workspace) -> Optional[AdminOwnerSummary]:
    owner_node_id = await get_workspace_owner_user_id(ws.id)
    if not owner_node_id:
        return None
    summaries = await _owner_summaries_for_ids([owner_node_id])
    return summaries.get(owner_node_id)


async def _hydrate_workspace_list_item(
    ws: Workspace,
    *,
    owner: Optional[AdminOwnerSummary] = None,
) -> AdminWorkspaceListItem:
    member_count, app_count, track_count = await _workspace_counts(ws)
    if owner is None:
        owner = await _workspace_owner_summary(ws)
    return AdminWorkspaceListItem(
        id=ws.id,
        kind=getattr(ws, "kind", "") or "",
        name=getattr(ws, "name", "") or "",
        workspace_type=getattr(ws, "workspace_type", "") or "",
        member_count=member_count,
        app_count=app_count,
        track_count=track_count,
        created_at=getattr(ws, "created_at", None),
        owner=owner,
    )


async def list_admin_workspaces(
    *,
    page: int = 1,
    per_page: int = 20,
    search: Optional[str] = None,
    kind: Optional[str] = None,
) -> AdminWorkspaceListResponse:
    """Paginated platform-wide workspace directory."""
    page = max(1, page)
    per_page = max(1, min(per_page, 100))
    filters: Dict[str, Any] = {}
    if kind and kind.strip().lower() in ("personal", "organization"):
        filters["context.kind"] = kind.strip().lower()
    if search and search.strip():
        filters["context.name"] = {
            "$regex": re.escape(search.strip()),
            "$options": "i",
        }
    pager = ObjectPager(Workspace, page_size=per_page, filters=filters or None)
    rows: List[Workspace] = await pager.get_page(page=page)
    pagination = pager.to_dict()
    owner_node_ids = [await get_workspace_owner_user_id(ws.id) for ws in rows]
    owner_map = await _owner_summaries_for_ids(owner_node_ids)
    items = [
        await _hydrate_workspace_list_item(
            ws,
            owner=owner_map.get(owner_node_id) if owner_node_id else None,
        )
        for ws, owner_node_id in zip(rows, owner_node_ids)
    ]
    return AdminWorkspaceListResponse(
        workspaces=items,
        total=pagination["total_items"],
        page=pagination["current_page"],
        per_page=pagination["page_size"],
        total_pages=pagination["total_pages"],
        has_previous=pagination["has_previous"],
        has_next=pagination["has_next"],
    )


async def get_admin_workspace_detail(workspace_id: str) -> AdminWorkspaceDetail:
    """Return the admin detail view for a single workspace."""
    from app.api.errors import ResourceNotFoundError

    ws = await Workspace.get(workspace_id)
    if not ws:
        raise ResourceNotFoundError(message="Workspace not found")
    member_count, app_count, track_count = await _workspace_counts(ws)
    owner = await _workspace_owner_summary(ws)
    return AdminWorkspaceDetail(
        id=ws.id,
        kind=getattr(ws, "kind", "") or "",
        name=getattr(ws, "name", "") or "",
        workspace_type=getattr(ws, "workspace_type", "") or "",
        description=getattr(ws, "description", "") or "",
        member_count=member_count,
        app_count=app_count,
        track_count=track_count,
        created_at=getattr(ws, "created_at", None),
        updated_at=getattr(ws, "updated_at", None),
        owner=owner,
    )


async def update_admin_workspace(
    workspace_id: str,
    body: AdminWorkspaceUpdate,
) -> AdminWorkspaceDetail:
    """Apply admin edits to a workspace and return the refreshed detail."""
    from app.api.errors import ResourceNotFoundError

    ws = await Workspace.get(workspace_id)
    if not ws:
        raise ResourceNotFoundError(message="Workspace not found")
    if body.name is not None:
        ws.name = body.name.strip()
        ws.name_fold = ws.name.casefold()
    if body.description is not None:
        ws.description = body.description
    if body.workspace_type is not None:
        ws.workspace_type = body.workspace_type.strip()
    from datetime import datetime, timezone

    ws.updated_at = datetime.now(timezone.utc).isoformat()
    await ws.save()
    return await get_admin_workspace_detail(workspace_id)


async def delete_admin_workspace(workspace_id: str) -> None:
    """Cascade-delete a workspace and all resources under it."""
    from app.api.errors import ResourceNotFoundError

    ws = await Workspace.get(workspace_id)
    if not ws:
        raise ResourceNotFoundError(message="Workspace not found")
    await delete_workspace_cascade(ws, cascade=True)


async def _auth_email(user: User) -> str:
    if not user.user_id:
        return ""
    try:
        auth_user = await AuthUser.get(user.user_id)
    except Exception:
        return ""
    return (getattr(auth_user, "email", "") or "").strip()


async def list_admin_workspace_members(
    workspace_id: str,
    *,
    page: int = 1,
    per_page: int = 50,
    search: Optional[str] = None,
) -> AdminMemberListResponse:
    """Return a paginated, optionally-searched list of workspace members."""
    from app.api.errors import ResourceNotFoundError

    ws = await Workspace.get(workspace_id)
    if not ws:
        raise ResourceNotFoundError(message="Workspace not found")
    try:
        members = await ws.nodes(edge=[IS_MEMBER_OF], direction="in", node=["User"])
    except Exception:
        members = []

    ctx = await ws.get_context()
    rows: List[AdminMemberListItem] = []
    for member in members:
        role = "member"
        try:
            edges = await ctx.find_edges_between(
                member.id, ws.id, edge_class=IS_MEMBER_OF
            )
            if edges:
                role = getattr(edges[0], "role", None) or "member"
        except Exception:
            pass
        email = await _auth_email(member)
        display_name = member.display_name or ""
        if search and search.strip():
            term = search.strip().casefold()
            hay = f"{display_name} {email} {role}".casefold()
            if term not in hay:
                continue
        rows.append(
            AdminMemberListItem(
                id=member.id,
                user_id=member.user_id or None,
                email=email,
                display_name=display_name,
                role=str(role),
            )
        )

    page = max(1, page)
    per_page = max(1, min(per_page, 100))
    total = len(rows)
    total_pages = max(1, (total + per_page - 1) // per_page) if total else 0
    start = (page - 1) * per_page
    end = start + per_page
    page_rows = rows[start:end]
    return AdminMemberListResponse(
        members=page_rows,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        has_previous=page > 1,
        has_next=end < total,
    )


async def _workspace_name_map(workspace_ids: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for ws_id in workspace_ids:
        if not ws_id or ws_id in out:
            continue
        ws = await Workspace.get(ws_id)
        out[ws_id] = getattr(ws, "name", "") if ws else ""
    return out


async def list_admin_apps(
    *,
    page: int = 1,
    per_page: int = 20,
    search: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> AdminResourceListResponse:
    """Return a paginated, filterable admin list of apps."""
    page = max(1, page)
    per_page = max(1, min(per_page, 100))
    filters: Dict[str, Any] = {}
    if workspace_id:
        filters["context.workspace_id"] = workspace_id
    if search and search.strip():
        filters["context.name"] = {
            "$regex": re.escape(search.strip()),
            "$options": "i",
        }
    pager = ObjectPager(App, page_size=per_page, filters=filters or None)
    rows: List[App] = await pager.get_page(page=page)
    pagination = pager.to_dict()
    ws_ids = [getattr(r, "workspace_id", "") or "" for r in rows]
    ws_names = await _workspace_name_map(ws_ids)
    owner_ids = [getattr(r, "owner_user_id", None) for r in rows]
    owner_map = await _owner_summaries_for_ids(owner_ids)
    items = [
        AdminResourceListItem(
            id=r.id,
            name=_resource_label(r, kind="app"),
            workspace_id=getattr(r, "workspace_id", "") or "",
            workspace_name=ws_names.get(getattr(r, "workspace_id", "") or "", ""),
            created_at=getattr(r, "created_at", None),
            owner=owner_map.get(getattr(r, "owner_user_id", "") or ""),
        )
        for r in rows
    ]
    return AdminResourceListResponse(
        items=items,
        total=pagination["total_items"],
        page=pagination["current_page"],
        per_page=pagination["page_size"],
        total_pages=pagination["total_pages"],
        has_previous=pagination["has_previous"],
        has_next=pagination["has_next"],
    )


async def get_admin_app_detail(app_id: str) -> AdminResourceDetail:
    """Return the admin detail view for a single app."""
    from app.api.errors import ResourceNotFoundError

    app = await App.get(app_id)
    if not app:
        raise ResourceNotFoundError(message="App not found")
    ws_id = getattr(app, "workspace_id", "") or ""
    ws_names = await _workspace_name_map([ws_id])
    owner_id = getattr(app, "owner_user_id", None)
    owner_map = await _owner_summaries_for_ids([owner_id])
    owner = owner_map.get(owner_id) if owner_id else None
    return AdminResourceDetail(
        id=app.id,
        resource_type="app",
        name=_resource_label(app, kind="app"),
        workspace_id=ws_id,
        workspace_name=ws_names.get(ws_id, ""),
        visibility=getattr(app, "visibility", "") or "",
        owner_id=owner_id,
        owner=owner,
        created_at=getattr(app, "created_at", None),
        updated_at=getattr(app, "updated_at", None),
    )


async def get_admin_track_detail(track_id: str) -> AdminResourceDetail:
    """Return the admin detail view for a single track."""
    from app.api.errors import ResourceNotFoundError

    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")
    ws_id = getattr(track, "workspace_id", "") or ""
    ws_names = await _workspace_name_map([ws_id])
    owner_id = getattr(track, "owner_id", None)
    owner_map = await _owner_summaries_for_ids([owner_id])
    owner = owner_map.get(owner_id) if owner_id else None
    return AdminResourceDetail(
        id=track.id,
        resource_type="track",
        name=_resource_label(track, kind="track"),
        workspace_id=ws_id,
        workspace_name=ws_names.get(ws_id, ""),
        visibility=getattr(track, "visibility", "") or "",
        owner_id=owner_id,
        owner=owner,
        created_at=getattr(track, "created_at", None),
        updated_at=getattr(track, "updated_at", None),
    )


async def list_admin_tracks(
    *,
    page: int = 1,
    per_page: int = 20,
    search: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> AdminResourceListResponse:
    """Return a paginated, filterable admin list of tracks."""
    page = max(1, page)
    per_page = max(1, min(per_page, 100))
    filters: Dict[str, Any] = {}
    if workspace_id:
        filters["context.workspace_id"] = workspace_id
    if search and search.strip():
        filters["context.title"] = {
            "$regex": re.escape(search.strip()),
            "$options": "i",
        }
    pager = ObjectPager(Track, page_size=per_page, filters=filters or None)
    rows: List[Track] = await pager.get_page(page=page)
    pagination = pager.to_dict()
    ws_ids = [getattr(r, "workspace_id", "") or "" for r in rows]
    ws_names = await _workspace_name_map(ws_ids)
    owner_ids = [getattr(r, "owner_id", None) for r in rows]
    owner_map = await _owner_summaries_for_ids(owner_ids)
    items = [
        AdminResourceListItem(
            id=r.id,
            name=_resource_label(r, kind="track"),
            workspace_id=getattr(r, "workspace_id", "") or "",
            workspace_name=ws_names.get(getattr(r, "workspace_id", "") or "", ""),
            created_at=getattr(r, "created_at", None),
            owner=owner_map.get(getattr(r, "owner_id", "") or ""),
        )
        for r in rows
    ]
    return AdminResourceListResponse(
        items=items,
        total=pagination["total_items"],
        page=pagination["current_page"],
        per_page=pagination["page_size"],
        total_pages=pagination["total_pages"],
        has_previous=pagination["has_previous"],
        has_next=pagination["has_next"],
    )
