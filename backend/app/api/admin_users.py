"""Platform admin user management endpoints."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Request
from jvspatial.api import endpoint
from pydantic import ValidationError

from app.api.errors import BadRequestError, MissingAuthenticationError
from app.api.utils import export_node, require_platform_admin, resolve_principal_id
from app.schemas.admin import (
    AdminUserCreate,
    AdminUserDeleteRequest,
    AdminUserDeleteResponse,
    AdminUserUpdate,
)
from app.services.admin_users import (
    create_admin_user,
    deactivate_admin_user,
    demote_platform_admin,
    get_admin_overview,
    get_admin_user_detail,
    list_admin_users,
    promote_platform_admin,
    reactivate_admin_user,
    resolve_user_for_admin,
    update_admin_user,
)
from app.services.change_event import emit_change_event
from app.services.user_lifecycle import (
    _auth_email_for_user,
    delete_user_account_as_admin,
    get_account_deletion_preview_for_admin,
)


@endpoint("/admin/overview", methods=["GET"], auth=True, tags=["Admin"])
async def admin_overview(request: Request) -> Dict[str, Any]:
    """Platform-wide counts for the admin dashboard."""
    require_platform_admin(request)
    overview = await get_admin_overview()
    return overview.model_dump()


@endpoint("/admin/users", methods=["GET"], auth=True, tags=["Admin"])
async def admin_list_users(
    request: Request,
    page: int = 1,
    per_page: int = 20,
    search: Optional[str] = None,
    status: str = "all",
    platform_admin: Optional[bool] = None,
) -> Dict[str, Any]:
    """Paginated platform-wide user directory."""
    require_platform_admin(request)
    result = await list_admin_users(
        page=page,
        per_page=per_page,
        search=search,
        status=status,
        platform_admin=platform_admin,
    )
    return result.model_dump()


@endpoint("/admin/users", methods=["POST"], auth=True, tags=["Admin"])
async def admin_create_user(request: Request) -> Dict[str, Any]:
    """Create AuthUser + graph User + personal workspace."""
    require_platform_admin(request)
    actor_id = resolve_principal_id(request)
    if not actor_id:
        raise MissingAuthenticationError(message="Authentication required")
    try:
        body = AdminUserCreate.model_validate(await request.json())
    except ValidationError as e:
        raise BadRequestError(
            message="Validation failed for admin user create body",
            details={"errors": e.errors()},
        )
    detail = await create_admin_user(body)
    await emit_change_event(
        actor_kind="human",
        actor_id=actor_id,
        action="user.create",
        resource_type="User",
        resource_id=detail.id,
        before=None,
        after=detail.model_dump(),
        scope=f"admin:user:{detail.id}",
        details={"admin_action": True},
    )
    return {"user": detail.model_dump(), "message": "User created successfully"}


@endpoint("/admin/users/{user_id}", methods=["GET"], auth=True, tags=["Admin"])
async def admin_get_user(request: Request, user_id: str) -> Dict[str, Any]:
    """Full admin profile for a user."""
    require_platform_admin(request)
    detail = await get_admin_user_detail(user_id)
    return {"user": detail.model_dump()}


@endpoint("/admin/users/{user_id}", methods=["PATCH"], auth=True, tags=["Admin"])
async def admin_patch_user(
    request: Request,
    user_id: str,
) -> Dict[str, Any]:
    """Admin update of user profile, roles, or password."""
    require_platform_admin(request)
    actor_id = resolve_principal_id(request)
    if not actor_id:
        raise MissingAuthenticationError(message="Authentication required")
    try:
        body = AdminUserUpdate.model_validate(await request.json())
    except ValidationError as e:
        raise BadRequestError(
            message="Validation failed for admin user update body",
            details={"errors": e.errors()},
        )
    before_user = await resolve_user_for_admin(user_id)
    before_snapshot = await export_node(before_user)
    detail = await update_admin_user(user_id, body, actor_id=actor_id)
    await emit_change_event(
        actor_kind="human",
        actor_id=actor_id,
        action="user.update",
        resource_type="User",
        resource_id=detail.id,
        before=before_snapshot,
        after=detail.model_dump(),
        scope=f"admin:user:{detail.id}",
        details={"admin_action": True},
    )
    return {"user": detail.model_dump(), "message": "User updated successfully"}


@endpoint(
    "/admin/users/{user_id}/deactivate",
    methods=["POST"],
    auth=True,
    tags=["Admin"],
)
async def admin_deactivate_user(request: Request, user_id: str) -> Dict[str, Any]:
    """Soft-deactivate a user (blocks login)."""
    require_platform_admin(request)
    actor_id = resolve_principal_id(request)
    if not actor_id:
        raise MissingAuthenticationError(message="Authentication required")
    before_user = await resolve_user_for_admin(user_id)
    before_snapshot = await export_node(before_user)
    detail = await deactivate_admin_user(user_id, actor_id=actor_id)
    # Deactivation blocks new logins; also end the sessions already open —
    # a deactivated account must not keep working until its tokens expire.
    if detail.user_id:
        from app.api.auth import _get_auth_service

        await _get_auth_service().revoke_all_user_tokens(detail.user_id)
    await emit_change_event(
        actor_kind="human",
        actor_id=actor_id,
        action="user.update",
        resource_type="User",
        resource_id=detail.id,
        before=before_snapshot,
        after=detail.model_dump(),
        scope=f"admin:user:{detail.id}",
        details={"admin_action": True, "deactivated": True},
    )
    return {"user": detail.model_dump(), "message": "User deactivated successfully"}


@endpoint(
    "/admin/users/{user_id}/reactivate",
    methods=["POST"],
    auth=True,
    tags=["Admin"],
)
async def admin_reactivate_user(request: Request, user_id: str) -> Dict[str, Any]:
    """Re-enable a deactivated user."""
    require_platform_admin(request)
    actor_id = resolve_principal_id(request)
    if not actor_id:
        raise MissingAuthenticationError(message="Authentication required")
    before_user = await resolve_user_for_admin(user_id)
    before_snapshot = await export_node(before_user)
    detail = await reactivate_admin_user(user_id)
    await emit_change_event(
        actor_kind="human",
        actor_id=actor_id,
        action="user.update",
        resource_type="User",
        resource_id=detail.id,
        before=before_snapshot,
        after=detail.model_dump(),
        scope=f"admin:user:{detail.id}",
        details={"admin_action": True, "reactivated": True},
    )
    return {"user": detail.model_dump(), "message": "User reactivated successfully"}


@endpoint(
    "/admin/users/{user_id}/promote-admin",
    methods=["POST"],
    auth=True,
    tags=["Admin"],
)
async def admin_promote_user(request: Request, user_id: str) -> Dict[str, Any]:
    """Grant platform admin role."""
    require_platform_admin(request)
    actor_id = resolve_principal_id(request)
    if not actor_id:
        raise MissingAuthenticationError(message="Authentication required")
    before_user = await resolve_user_for_admin(user_id)
    before_snapshot = await export_node(before_user)
    detail = await promote_platform_admin(user_id)
    await emit_change_event(
        actor_kind="human",
        actor_id=actor_id,
        action="user.update",
        resource_type="User",
        resource_id=detail.id,
        before=before_snapshot,
        after=detail.model_dump(),
        scope=f"admin:user:{detail.id}",
        details={"admin_action": True, "promoted_platform_admin": True},
    )
    return {"user": detail.model_dump(), "message": "Platform admin role granted"}


@endpoint(
    "/admin/users/{user_id}/demote-admin",
    methods=["POST"],
    auth=True,
    tags=["Admin"],
)
async def admin_demote_user(request: Request, user_id: str) -> Dict[str, Any]:
    """Revoke platform admin role."""
    require_platform_admin(request)
    actor_id = resolve_principal_id(request)
    if not actor_id:
        raise MissingAuthenticationError(message="Authentication required")
    before_user = await resolve_user_for_admin(user_id)
    before_snapshot = await export_node(before_user)
    detail = await demote_platform_admin(user_id, actor_id=actor_id)
    await emit_change_event(
        actor_kind="human",
        actor_id=actor_id,
        action="user.update",
        resource_type="User",
        resource_id=detail.id,
        before=before_snapshot,
        after=detail.model_dump(),
        scope=f"admin:user:{detail.id}",
        details={"admin_action": True, "demoted_platform_admin": True},
    )
    return {"user": detail.model_dump(), "message": "Platform admin role revoked"}


@endpoint(
    "/admin/users/{user_id}/deletion-preview",
    methods=["GET"],
    auth=True,
    tags=["Admin"],
)
async def admin_user_deletion_preview(
    request: Request,
    user_id: str,
) -> Dict[str, Any]:
    """Pre-flight impact summary for admin hard-delete."""
    require_platform_admin(request)
    user = await resolve_user_for_admin(user_id)
    preview = await get_account_deletion_preview_for_admin(user.id)
    return preview.model_dump()


@endpoint(
    "/admin/users/{user_id}/delete",
    methods=["POST"],
    auth=True,
    tags=["Admin"],
)
async def admin_delete_user(request: Request, user_id: str) -> Dict[str, Any]:
    """Permanently delete a user account (admin action)."""
    require_platform_admin(request)
    actor_id = resolve_principal_id(request)
    if not actor_id:
        raise MissingAuthenticationError(message="Authentication required")
    try:
        body = AdminUserDeleteRequest.model_validate(await request.json())
    except ValidationError as e:
        raise BadRequestError(
            message="Validation failed for admin user delete body",
            details={"errors": e.errors()},
        )
    before_user = await resolve_user_for_admin(user_id)
    before_snapshot = await export_node(before_user)
    result = await delete_user_account_as_admin(
        user_id,
        actor_id=actor_id,
        confirm_email=body.confirm_email,
        force=body.force,
    )
    await emit_change_event(
        actor_kind="human",
        actor_id=actor_id,
        action="user.delete",
        resource_type="User",
        resource_id=result.deleted_user_node_id,
        before=before_snapshot,
        after=None,
        scope=f"admin:user:{result.deleted_user_node_id}",
        details={"admin_action": True},
    )
    return AdminUserDeleteResponse(
        message="User deleted successfully",
        deleted_user_id=result.deleted_user_id,
    ).model_dump()


@endpoint(
    "/admin/users/{user_id}/send-password-reset",
    methods=["POST"],
    auth=True,
    tags=["Admin"],
)
async def admin_send_password_reset(request: Request, user_id: str) -> Dict[str, Any]:
    """Email a password-reset link to the target user."""
    require_platform_admin(request)
    actor_id = resolve_principal_id(request)
    if not actor_id:
        raise MissingAuthenticationError(message="Authentication required")

    user = await resolve_user_for_admin(user_id)
    email = await _auth_email_for_user(user)
    if not email:
        raise BadRequestError(message="Cannot send reset — no email on this account")

    from app.services.password_reset import create_reset_request

    await create_reset_request(email)

    await emit_change_event(
        actor_kind="human",
        actor_id=actor_id,
        action="user.update",
        resource_type="User",
        resource_id=user.id,
        before=None,
        after=None,
        scope=f"admin:user:{user.id}",
        details={"admin_action": True, "password_reset_sent": True},
    )

    return {
        "message": "Password reset email sent if the account is active.",
        "email": email,
    }
