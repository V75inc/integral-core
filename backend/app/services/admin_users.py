"""Platform admin user management service."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from jvspatial.api.auth.models import User as AuthUser
from jvspatial.api.auth.models import UserCreate
from jvspatial.core.pager import ObjectPager

from app.config import settings
from app.models.nodes import App, Track, User, Workspace
from app.schemas.admin import (
    AdminOverview,
    AdminUserCreate,
    AdminUserDetail,
    AdminUserListItem,
    AdminUserListResponse,
    AdminUserUpdate,
)
from app.services.permissions import get_user_node
from app.services.user_lifecycle import (
    _org_membership_summaries,
    _personal_workspace_summaries,
    _revoke_all_oauth_grants,
)

logger = logging.getLogger(__name__)

ADMIN_ROLE = "admin"


def _iso_timestamp(value: Any) -> Optional[str]:
    """Coerce AuthUser/datetime timestamps to ISO strings for API schemas."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return str(value)


async def _auth_user_for_graph_user(user: User) -> Optional[AuthUser]:
    if not user.user_id:
        return None
    try:
        return await AuthUser.get(user.user_id)
    except Exception:
        return None


def _is_platform_admin_auth(auth_user: Optional[AuthUser]) -> bool:
    if not auth_user:
        return False
    roles = list(getattr(auth_user, "roles", None) or [])
    return ADMIN_ROLE in roles


async def count_platform_admins() -> int:
    """Count AuthUsers carrying the platform admin role."""
    count = 0
    try:
        rows = await AuthUser.find({})
    except Exception:
        logger.exception("count_platform_admins failed")
        return 0
    for row in rows:
        if _is_platform_admin_auth(row):
            count += 1
    return count


async def _hydrate_list_item(user: User) -> AdminUserListItem:
    auth_user = await _auth_user_for_graph_user(user)
    return AdminUserListItem(
        id=user.id,
        user_id=user.user_id or None,
        email=(getattr(auth_user, "email", "") or "") if auth_user else "",
        display_name=user.display_name or "",
        is_active=getattr(auth_user, "is_active", True) if auth_user else True,
        is_platform_admin=_is_platform_admin_auth(auth_user),
        email_verified=bool(getattr(user, "email_verified", False)),
        created_at=_iso_timestamp(getattr(user, "created_at", None)),
        last_accessed=_iso_timestamp(
            getattr(auth_user, "last_accessed", None) if auth_user else None
        ),
    )


async def _matching_user_ids_for_search(search: str) -> Set[str]:
    """Resolve graph User ids matching display_name or linked AuthUser email."""
    term = (search or "").strip()
    if not term:
        return set()

    ids: Set[str] = set()
    name_filter = {"context.display_name": {"$regex": re.escape(term), "$options": "i"}}
    for row in await User.find(name_filter):
        ids.add(row.id)

    email_filter = {"context.email": {"$regex": re.escape(term), "$options": "i"}}
    try:
        auth_rows = await AuthUser.find(email_filter)
    except Exception:
        auth_rows = []
    for auth_row in auth_rows:
        linked = await User.find({"context.user_id": auth_row.id})
        for user in linked:
            ids.add(user.id)
    return ids


async def _auth_user_ids_for_status(status: str) -> Optional[Set[str]]:
    """Return linked graph-user auth ids for active/inactive filter, or None for all."""
    normalized = (status or "all").strip().lower()
    if normalized == "all":
        return None
    active_flag = normalized == "active"
    try:
        rows = await AuthUser.find({"context.is_active": active_flag})
    except Exception:
        rows = []
    return {row.id for row in rows}


async def _auth_user_ids_for_platform_admin_filter(enabled: bool) -> Set[str]:
    try:
        rows = await AuthUser.find({})
    except Exception:
        return set()
    out: Set[str] = set()
    for row in rows:
        if _is_platform_admin_auth(row) == enabled:
            out.add(row.id)
    return out


async def list_admin_users(
    *,
    page: int = 1,
    per_page: int = 20,
    search: Optional[str] = None,
    status: str = "all",
    platform_admin: Optional[bool] = None,
) -> AdminUserListResponse:
    """Paginated platform-wide user directory with admin filters."""
    page = max(1, page)
    per_page = max(1, min(per_page, 100))

    filters: Dict[str, Any] = {}
    if search and search.strip():
        matching_ids = await _matching_user_ids_for_search(search)
        if not matching_ids:
            return AdminUserListResponse(
                users=[],
                total=0,
                page=page,
                per_page=per_page,
                total_pages=0,
                has_previous=False,
                has_next=False,
            )
        filters["id"] = {"$in": list(matching_ids)}

    status_auth_ids = await _auth_user_ids_for_status(status)
    if status_auth_ids is not None:
        status_users = await User.find(
            {"context.user_id": {"$in": list(status_auth_ids)}}
        )
        status_node_ids = {u.id for u in status_users}
        if not status_auth_ids:
            status_node_ids = set()
        if "id" in filters:
            filters["id"]["$in"] = [
                uid for uid in filters["id"]["$in"] if uid in status_node_ids
            ]
        else:
            filters["id"] = {"$in": list(status_node_ids)}

    if platform_admin is not None:
        admin_auth_ids = await _auth_user_ids_for_platform_admin_filter(platform_admin)
        admin_users = await User.find(
            {"context.user_id": {"$in": list(admin_auth_ids)}}
        )
        admin_node_ids = {u.id for u in admin_users}
        if "id" in filters:
            filters["id"]["$in"] = [
                uid for uid in filters["id"]["$in"] if uid in admin_node_ids
            ]
        else:
            filters["id"] = {"$in": list(admin_node_ids)}

    pager = ObjectPager(User, page_size=per_page, filters=filters or None)
    users: List[User] = await pager.get_page(page=page)
    pagination = pager.to_dict()

    items = [await _hydrate_list_item(u) for u in users]
    return AdminUserListResponse(
        users=items,
        total=pagination["total_items"],
        page=pagination["current_page"],
        per_page=pagination["page_size"],
        total_pages=pagination["total_pages"],
        has_previous=pagination["has_previous"],
        has_next=pagination["has_next"],
    )


async def get_admin_user_detail(user_id: str) -> AdminUserDetail:
    """Load full admin profile for a graph User or AuthUser id."""
    from app.api.errors import ResourceNotFoundError

    user = await get_user_node(user_id)
    if not user:
        user = await User.get(user_id)
    if not user:
        raise ResourceNotFoundError(message="User not found")

    auth_user = await _auth_user_for_graph_user(user)
    org_memberships = await _org_membership_summaries(user)
    personal_workspaces = await _personal_workspace_summaries(user)
    roles = list(getattr(auth_user, "roles", None) or []) if auth_user else []

    return AdminUserDetail(
        id=user.id,
        user_id=user.user_id or None,
        email=(getattr(auth_user, "email", "") or "") if auth_user else "",
        display_name=user.display_name or "",
        avatar_url=getattr(user, "avatar_url", "") or "",
        is_active=getattr(auth_user, "is_active", True) if auth_user else True,
        is_platform_admin=_is_platform_admin_auth(auth_user),
        roles=roles,
        email_verified=bool(getattr(user, "email_verified", False)),
        created_at=_iso_timestamp(getattr(user, "created_at", None)),
        updated_at=_iso_timestamp(getattr(user, "updated_at", None)),
        last_accessed=_iso_timestamp(
            getattr(auth_user, "last_accessed", None) if auth_user else None
        ),
        org_memberships=org_memberships,
        personal_workspaces=personal_workspaces,
    )


async def update_admin_user(
    user_id: str,
    body: AdminUserUpdate,
    *,
    actor_id: str,
) -> AdminUserDetail:
    """Admin update of graph User + AuthUser fields."""
    from app.api.errors import (
        BadRequestError,
        ResourceConflictError,
        ResourceNotFoundError,
    )

    user = await get_user_node(user_id)
    if not user:
        user = await User.get(user_id)
    if not user:
        raise ResourceNotFoundError(message="User not found")

    auth_user = await _auth_user_for_graph_user(user)
    if body.password is not None:
        if not auth_user:
            raise BadRequestError(
                message="Cannot set password — no linked auth account"
            )
        if len(body.password) < settings.PASSWORD_MIN_LENGTH:
            raise BadRequestError(
                message=f"Password must be at least {settings.PASSWORD_MIN_LENGTH} characters"
            )
        from app.api.auth import _get_auth_service

        auth_user.password_hash = _get_auth_service()._hash_password(body.password)
        await auth_user.save()

    if body.display_name is not None:
        user.display_name = body.display_name.strip()
        user.updated_at = datetime.now(timezone.utc).isoformat()
        await user.save()
        if auth_user and getattr(auth_user, "name", None) != user.display_name:
            auth_user.name = user.display_name
            await auth_user.save()

    if body.email_verified is not None:
        user.email_verified = body.email_verified
        user.updated_at = datetime.now(timezone.utc).isoformat()
        await user.save()

    if body.roles is not None:
        if not auth_user:
            raise BadRequestError(
                message="Cannot update roles — no linked auth account"
            )
        normalized = [str(r).strip() for r in body.roles if str(r).strip()]
        if ADMIN_ROLE not in normalized and _is_platform_admin_auth(auth_user):
            remaining = await count_platform_admins()
            if remaining <= 1:
                raise ResourceConflictError(
                    message="Cannot demote the last platform admin"
                )
        auth_user.roles = normalized
        await auth_user.save()

    _ = actor_id
    return await get_admin_user_detail(user.id)


async def create_admin_user(body: AdminUserCreate) -> AdminUserDetail:
    """Create AuthUser + graph User + personal workspace."""
    from app.api.auth import _get_auth_service
    from app.api.errors import BadRequestError
    from app.services.app_graph import catalog_user
    from app.services.personal_workspace import ensure_personal_workspace

    auth_service = _get_auth_service()
    try:
        user_response = await auth_service.register_user(
            UserCreate(email=body.email, password=body.password)
        )
    except ValueError as e:
        raise BadRequestError(message=str(e))

    auth_user_id = user_response.id
    now_iso = datetime.now(timezone.utc).isoformat()
    user_node = await User.create(
        user_id=auth_user_id,
        display_name=body.display_name.strip(),
        created_at=now_iso,
        updated_at=now_iso,
    )
    await catalog_user(user_node)
    try:
        await ensure_personal_workspace(user_node)
    except Exception:
        logger.exception("ensure_personal_workspace failed during admin user create")
    return await get_admin_user_detail(user_node.id)


async def deactivate_admin_user(user_id: str, *, actor_id: str) -> AdminUserDetail:
    """Soft-deactivate a user (blocks login)."""
    from app.api.errors import (
        BadRequestError,
        ResourceConflictError,
        ResourceNotFoundError,
    )

    user = await get_user_node(user_id)
    if not user:
        user = await User.get(user_id)
    if not user:
        raise ResourceNotFoundError(message="User not found")
    if user.user_id and user.user_id == actor_id:
        raise BadRequestError(message="Cannot deactivate your own account")

    auth_user = await _auth_user_for_graph_user(user)
    if not auth_user:
        raise BadRequestError(message="Cannot deactivate — no linked auth account")
    if _is_platform_admin_auth(auth_user):
        remaining_active_admins = 0
        for row in await AuthUser.find({"context.is_active": True}):
            if _is_platform_admin_auth(row):
                remaining_active_admins += 1
        if remaining_active_admins <= 1:
            raise ResourceConflictError(
                message="Cannot deactivate the last active platform admin"
            )

    auth_user.is_active = False
    await auth_user.save()
    principal = user.user_id or user.id
    await _revoke_all_oauth_grants(principal)
    return await get_admin_user_detail(user.id)


async def reactivate_admin_user(user_id: str) -> AdminUserDetail:
    """Re-enable a deactivated user."""
    from app.api.errors import BadRequestError, ResourceNotFoundError

    user = await get_user_node(user_id)
    if not user:
        user = await User.get(user_id)
    if not user:
        raise ResourceNotFoundError(message="User not found")

    auth_user = await _auth_user_for_graph_user(user)
    if not auth_user:
        raise BadRequestError(message="Cannot reactivate — no linked auth account")
    auth_user.is_active = True
    await auth_user.save()
    return await get_admin_user_detail(user.id)


async def promote_platform_admin(user_id: str) -> AdminUserDetail:
    """Grant platform admin role."""
    detail = await get_admin_user_detail(user_id)
    if detail.is_platform_admin:
        return detail
    roles = list(detail.roles or [])
    if ADMIN_ROLE not in roles:
        roles.append(ADMIN_ROLE)
    return await update_admin_user(
        detail.id,
        AdminUserUpdate(roles=roles),
        actor_id="admin",
    )


async def demote_platform_admin(user_id: str, *, actor_id: str) -> AdminUserDetail:
    """Revoke platform admin role."""
    from app.api.errors import BadRequestError, ResourceConflictError

    detail = await get_admin_user_detail(user_id)
    if detail.user_id and detail.user_id == actor_id:
        raise BadRequestError(message="Cannot demote your own platform admin role")
    if not detail.is_platform_admin:
        return detail
    if await count_platform_admins() <= 1:
        raise ResourceConflictError(message="Cannot demote the last platform admin")
    roles = [r for r in detail.roles if r != ADMIN_ROLE]
    return await update_admin_user(
        detail.id,
        AdminUserUpdate(roles=roles),
        actor_id=actor_id,
    )


async def get_admin_overview() -> AdminOverview:
    """Platform-wide entity counts."""
    user_pager = ObjectPager(User, page_size=1)
    await user_pager.get_page(page=1)
    total_users = user_pager.to_dict()["total_items"]

    active_users = 0
    inactive_users = 0
    platform_admins = 0
    try:
        auth_rows = await AuthUser.find({})
    except Exception:
        auth_rows = []
    for row in auth_rows:
        if getattr(row, "is_active", True):
            active_users += 1
        else:
            inactive_users += 1
        if _is_platform_admin_auth(row):
            platform_admins += 1

    personal_ws_pager = ObjectPager(
        Workspace, page_size=1, filters={"context.kind": "personal"}
    )
    await personal_ws_pager.get_page(page=1)
    personal_workspaces = personal_ws_pager.to_dict()["total_items"]

    org_ws_pager = ObjectPager(
        Workspace, page_size=1, filters={"context.kind": "organization"}
    )
    await org_ws_pager.get_page(page=1)
    organization_workspaces = org_ws_pager.to_dict()["total_items"]

    app_pager = ObjectPager(App, page_size=1)
    await app_pager.get_page(page=1)
    total_apps = app_pager.to_dict()["total_items"]

    track_pager = ObjectPager(Track, page_size=1)
    await track_pager.get_page(page=1)
    total_tracks = track_pager.to_dict()["total_items"]

    return AdminOverview(
        total_users=total_users,
        active_users=active_users,
        inactive_users=inactive_users,
        platform_admins=platform_admins,
        personal_workspaces=personal_workspaces,
        organization_workspaces=organization_workspaces,
        total_apps=total_apps,
        total_tracks=total_tracks,
    )


async def resolve_user_for_admin(user_id: str) -> User:
    """Resolve graph User from id for admin mutations."""
    from app.api.errors import ResourceNotFoundError

    user = await get_user_node(user_id)
    if not user:
        user = await User.get(user_id)
    if not user:
        raise ResourceNotFoundError(message="User not found")
    return user
