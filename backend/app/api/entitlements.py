"""F3 Phase One — Entitlement grant / revoke / list (manual projection)."""

from typing import List

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import (
    InsufficientPermissionsError,
    MissingAuthenticationError,
)
from app.api.utils import is_platform_admin, resolve_principal_id
from app.schemas.entitlement import (
    EntitlementGrantRequest,
    EntitlementResponse,
    EntitlementRevokeRequest,
    EntitlementRevokeResponse,
)
from app.services.entitlements import (
    grant_entitlement,
    list_entitlements,
    revoke_entitlement,
)
from app.services.workspace_permissions import is_workspace_admin_or_owner


def _to_response(row) -> EntitlementResponse:
    return EntitlementResponse(
        id=row.id,
        workspace_id=row.workspace_id,
        entitlement_key=row.entitlement_key,
        package_slug=row.package_slug or "",
        status=row.status,
        source=row.source or "manual",
        on_loss=row.on_loss or "pause",
        data_access=row.data_access or "core_generic_read",
        retention=row.retention or "retain_until_uninstall",
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _authorize_workspace(
    request: Request, user_id: str, workspace_id: str
) -> None:
    if is_platform_admin(request):
        return
    if await is_workspace_admin_or_owner(user_id, workspace_id):
        return
    raise InsufficientPermissionsError(
        message="entitlement administer requires workspace admin/owner",
        details={"workspace_id": workspace_id},
    )


@endpoint("/entitlements", methods=["GET"], auth=True, tags=["Entitlements"])
async def get_list_entitlements(
    request: Request,
    workspace_id: str = "",
) -> List[EntitlementResponse]:
    """List entitlements for a workspace (admin/owner or platform admin)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    ws = (workspace_id or "").strip()
    if not ws:
        from app.api.errors import BadRequestError

        raise BadRequestError(message="workspace_id query param is required")
    await _authorize_workspace(request, user_id, ws)
    rows = await list_entitlements(workspace_id=ws)
    return [_to_response(r) for r in rows]


@endpoint("/entitlements/grant", methods=["POST"], auth=True, tags=["Entitlements"])
async def post_grant_entitlement(
    request: Request,
    workspace_id: str = "",
    entitlement_key: str = "",
    package_slug: str = "",
    expires_at: str = "",
) -> EntitlementResponse:
    """Flat body (jvspatial ParameterModelFactory) — mirrors EntitlementGrantRequest."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    body = EntitlementGrantRequest(
        workspace_id=workspace_id,
        entitlement_key=entitlement_key,
        package_slug=package_slug or None,
        expires_at=expires_at or None,
    )
    await _authorize_workspace(request, user_id, body.workspace_id)
    row = await grant_entitlement(
        workspace_id=body.workspace_id,
        entitlement_key=body.entitlement_key,
        package_slug=body.package_slug or body.entitlement_key,
        actor_id=user_id,
        expires_at=body.expires_at,
    )
    return _to_response(row)


@endpoint("/entitlements/revoke", methods=["POST"], auth=True, tags=["Entitlements"])
async def post_revoke_entitlement(
    request: Request,
    workspace_id: str = "",
    entitlement_key: str = "",
) -> EntitlementRevokeResponse:
    """Revoke entitlement and pause matching commercial Apps (Phase One)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    body = EntitlementRevokeRequest(
        workspace_id=workspace_id,
        entitlement_key=entitlement_key,
    )
    await _authorize_workspace(request, user_id, body.workspace_id)
    result = await revoke_entitlement(
        workspace_id=body.workspace_id,
        entitlement_key=body.entitlement_key,
        actor_id=user_id,
    )
    return EntitlementRevokeResponse(**result)
