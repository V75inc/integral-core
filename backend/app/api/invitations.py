"""Workspace invitation endpoints.

Owner/admin-issued invitations to join an organization-kind Workspace.
Acceptance is authenticated as the accepting user (token alone is not
enough to materialize the membership edge — the accepting principal MUST
be a signed-in User).

The response payload includes an ``acceptance_url`` for the issuer to
deliver out-of-band; email delivery happens inside ``create_invitation``
when configured.

NOTE: Do NOT add ``from __future__ import annotations`` here — the
jvspatial endpoint wrapper inspects ``param.annotation`` for ``Request``
types at runtime and string-form annotations (PEP 563) defeat that check.
"""

from typing import Any, Dict, List, Optional

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    MissingAuthenticationError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.api.utils import export_node, resolve_principal_id
from app.models.nodes import Invitation, Workspace
from app.services.invitations import (
    ERR_ALREADY_MEMBER,
    ERR_CONSUMED,
    ERR_DECLINED,
    ERR_DUPLICATE_PENDING,
    ERR_EMAIL_INVALID,
    ERR_EMAIL_MISMATCH,
    ERR_EXPIRED,
    ERR_GRANT_FAILED,
    ERR_INVALID_TOKEN,
    ERR_REVOKED,
    consume_invitation_token,
    create_invitation,
    decline_invitation,
    preview_invitation,
    revoke_invitation,
)
from app.services.permissions import get_user_node, member_edge_bool
from app.services.workspace_permissions import (
    can_access_workspace,
    is_workspace_owner,
)

_INVITE_ROLES = {"admin", "member", "guest"}


def _validate_invite_role(role: str) -> str:
    normalized = str(role or "").strip().lower()
    if normalized not in _INVITE_ROLES:
        raise BadRequestError(
            message=f"role must be one of: {', '.join(sorted(_INVITE_ROLES))}"
        )
    return normalized


async def _require_issuer(workspace: Workspace, user_node) -> str:
    """Return the issuer's role (owner|admin). Raise 403 if not authorized."""
    if await is_workspace_owner(user_node.id, workspace.id):
        return "owner"
    role = await can_access_workspace(user_node.id, workspace.id)
    if role == "admin":
        return "admin"
    raise InsufficientPermissionsError(
        message="Only workspace owners or admins can issue invitations"
    )


async def _require_member_for_read(workspace: Workspace, user_node) -> None:
    """List/revoke invitation metadata — owner or admin only (not guest)."""
    if await is_workspace_owner(user_node.id, workspace.id):
        return
    role = await can_access_workspace(user_node.id, workspace.id)
    if role == "admin":
        return
    raise InsufficientPermissionsError(message="Access denied")


def _mask_email_for_preview(email: str) -> str:
    """Redact invitee email on unauthenticated preview (token still required)."""
    normalized = (email or "").strip()
    if "@" not in normalized:
        return "***"
    local, domain = normalized.split("@", 1)
    masked_local = "*" if len(local) <= 1 else f"{local[0]}***"
    return f"{masked_local}@{domain}"


def _serialize_invitation(invitation: Invitation) -> Dict[str, Any]:
    return {
        "id": invitation.id,
        "workspace_id": invitation.workspace_id,
        "email": invitation.email,
        "invited_user_id": invitation.invited_user_id,
        "invited_by_user_id": invitation.invited_by_user_id,
        "role": invitation.role,
        "can_create_apps": bool(invitation.can_create_apps),
        "can_create_tracks": bool(invitation.can_create_tracks),
        "target_resource_type": invitation.target_resource_type or None,
        "target_resource_id": invitation.target_resource_id or None,
        "target_resource_role": invitation.target_resource_role or None,
        "status": invitation.status,
        "message": invitation.message,
        "created_at": invitation.created_at,
        "expires_at": invitation.expires_at,
        "consumed_at": invitation.consumed_at,
    }


def _serialize_invitation_preview(invitation: Invitation) -> Dict[str, Any]:
    """Minimal unauth preview — Full Sweep S8 reduces metadata leak surface."""
    return {
        "id": invitation.id,
        "email": _mask_email_for_preview(getattr(invitation, "email", None) or ""),
        "role": getattr(invitation, "role", None) or "member",
        "status": getattr(invitation, "status", None),
        "expires_at": getattr(invitation, "expires_at", None),
    }


def _err_to_status(code: str) -> int:
    if code == ERR_INVALID_TOKEN:
        return 404
    if code in (ERR_EXPIRED, ERR_REVOKED, ERR_DECLINED, ERR_CONSUMED):
        return 410
    if code == ERR_EMAIL_MISMATCH:
        return 403
    if code == ERR_ALREADY_MEMBER:
        return 422
    return 400


def _raise_accept_error(code: str) -> None:
    status_code = _err_to_status(code)
    if status_code == 404:
        raise ResourceNotFoundError(message="Invitation not found")
    if status_code == 403:
        raise InsufficientPermissionsError(
            message="This invitation was sent to a different email address."
        )
    if status_code == 410:
        raise BadRequestError(message=code)
    if status_code == 422:
        raise ResourceConflictError(message=code)
    if code == ERR_GRANT_FAILED:
        raise BadRequestError(
            message="Invitation could not be applied. Please try again or contact support."
        )
    raise BadRequestError(message=code)


@endpoint(
    "/workspaces/{workspace_id}/invitations",
    methods=["POST"],
    auth=True,
    tags=["Invitations"],
)
async def post_create_invitation(
    request: Request,
    workspace_id: str,
    email: str = "",
    role: str = "member",
    can_create_apps: bool = False,
    can_create_tracks: bool = False,
    can_create_spaces: bool = False,
    message: str = "",
) -> Dict[str, Any]:
    """Create a pending invitation. Owner/admin only.

    ``can_create_spaces`` is a legacy alias for ``can_create_apps`` —
    both map to the ``can_create_apps`` flag on the ``IS_MEMBER_OF`` edge.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    if not email or not email.strip():
        raise BadRequestError(message="email is required")
    validated_role = _validate_invite_role(role)
    workspace = await Workspace.get(workspace_id)
    if not workspace:
        raise ResourceNotFoundError(message="Workspace not found")
    from app.services.workspace_kind import is_collaborative_kind

    if not is_collaborative_kind(workspace.kind):
        raise BadRequestError(
            message="Only collaborative workspaces accept invitations"
        )
    user = await get_user_node(user_id)
    if not user:
        raise InsufficientPermissionsError(message="Access denied")
    await _require_issuer(workspace, user)

    # can_create_spaces is a legacy alias; either flag enables can_create_apps.
    effective_can_create_apps = can_create_apps or can_create_spaces

    try:
        invitation, plaintext, acceptance_url = await create_invitation(
            workspace=workspace,
            inviter_user_id=user_id,
            email=email,
            role=validated_role,
            can_create_apps=bool(effective_can_create_apps),
            can_create_tracks=bool(can_create_tracks),
            message=message,
            inviter_display_name=getattr(user, "display_name", None) or None,
        )
    except ValueError as exc:
        code = str(exc)
        if code == ERR_ALREADY_MEMBER:
            raise BadRequestError(message="User is already a member of this workspace")
        if code == ERR_DUPLICATE_PENDING:
            err = ResourceConflictError(
                message="A pending invitation for this email already exists",
                details={"entity": "invitation", "field": "email", "value": email},
            )
            err.error_code = "resource.duplicate_invitation"
            raise err
        if code == ERR_EMAIL_INVALID:
            raise BadRequestError(message="email must be a valid email address")
        raise BadRequestError(message=code)

    return {
        "invitation": _serialize_invitation(invitation),
        "acceptance_url": acceptance_url,
        "message": "Invitation created",
    }


@endpoint(
    "/workspaces/{workspace_id}/invitations",
    methods=["GET"],
    auth=True,
    tags=["Invitations"],
)
async def list_invitations(
    request: Request,
    workspace_id: str,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    """List invitations for the workspace. Owner or admin only."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    workspace = await Workspace.get(workspace_id)
    if not workspace:
        raise ResourceNotFoundError(message="Workspace not found")
    user = await get_user_node(user_id)
    if not user:
        raise InsufficientPermissionsError(message="Access denied")
    await _require_member_for_read(workspace, user)

    query: Dict[str, Any] = {"context.workspace_id": workspace.id}
    if status:
        query["context.status"] = status
    rows = await Invitation.find(query)
    items: List[Dict[str, Any]] = [_serialize_invitation(inv) for inv in rows]
    return {
        "invitations": items,
        "total": len(items),
        "workspace_id": workspace_id,
    }


@endpoint(
    "/workspaces/{workspace_id}/invitations/{invitation_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Invitations"],
)
async def delete_invitation(
    request: Request,
    workspace_id: str,
    invitation_id: str,
) -> Dict[str, Any]:
    """Revoke an invitation. Owner/admin or original inviter only."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    workspace = await Workspace.get(workspace_id)
    if not workspace:
        raise ResourceNotFoundError(message="Workspace not found")
    invitation = await Invitation.get(invitation_id)
    if not invitation or invitation.workspace_id != workspace.id:
        raise ResourceNotFoundError(message="Invitation not found")
    user = await get_user_node(user_id)
    if not user:
        raise InsufficientPermissionsError(message="Access denied")

    is_inviter = invitation.invited_by_user_id == user_id
    issuer_role = None
    if not is_inviter:
        issuer_role = await _require_issuer(workspace, user)

    await revoke_invitation(invitation, actor_user_id=user_id)

    return {
        "invitation": _serialize_invitation(invitation),
        "message": "Invitation revoked",
        "_issuer_role": issuer_role,
    }


@endpoint(
    "/invitations/{invitation_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Invitations"],
)
async def delete_resource_invitation(
    request: Request,
    invitation_id: str,
) -> Dict[str, Any]:
    """Phase 8 Plan 08-05 — resource-level invitation revoke (B2).

    Owner-only revoke for an Invitation that targets an App/Track/Entry.
    Workspace-targeted invitations MUST be revoked via
    ``DELETE /workspaces/{workspace_id}/invitations/{invitation_id}`` — this
    endpoint refuses them with a 400 so the two surfaces never race.

    REUSES the existing ``workspace.invitation_revoke`` ChangeEventAction
    Literal — every Invitation has a workspace_id, so the audit envelope is
    consistent with the workspace-level revoke flow. No new Literal members
    are introduced (D-05 single-Literal invariant preserved).
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    invitation = await Invitation.get(invitation_id)
    if not invitation:
        raise ResourceNotFoundError(message="Invitation not found")

    target_kind = (invitation.target_resource_type or "").strip().lower()
    target_id = (invitation.target_resource_id or "").strip()

    if not target_kind or not target_id:
        # Workspace-targeted invitation — route caller to the workspace endpoint.
        raise BadRequestError(
            message=(
                "Use DELETE /workspaces/{workspace_id}/invitations/"
                "{invitation_id} for workspace-targeted invitations."
            )
        )

    if target_kind not in ("app", "track", "entry"):
        raise BadRequestError(
            message=("target_resource_type must be one of: app, track, entry")
        )

    # Owner check on the target resource. Use the same 404 envelope as the
    # cross-owner negative-case (no existence leak — same idiom as Phase 1
    # connectors and Phase 3 policies endpoints).
    from app.services.permissions import resolve_role

    role = await resolve_role(user_id, target_kind, target_id)
    if role != "owner":
        raise ResourceNotFoundError(message="Invitation not found")

    await revoke_invitation(invitation, actor_user_id=user_id)

    return {
        "invitation": _serialize_invitation(invitation),
        "message": "Invitation revoked",
    }


@endpoint(
    "/invitations/{token}",
    methods=["GET"],
    auth=False,
    tags=["Invitations"],
)
async def get_invitation_preview(
    request: Request,
    token: str,
) -> Dict[str, Any]:
    """Unauthenticated preview of an invitation by plaintext token."""
    invitation, err = await preview_invitation(token)
    if err == ERR_INVALID_TOKEN or not invitation:
        raise ResourceNotFoundError(message="Invitation not found")
    workspace = await Workspace.get(invitation.workspace_id)
    workspace_summary = None
    if workspace:
        # Full Sweep S8: name + accent only — drop description from unauth preview.
        workspace_summary = {
            "id": workspace.id,
            "name": workspace.name,
            "accent_color": workspace.accent_color,
        }
    return {
        "invitation": _serialize_invitation_preview(invitation),
        "workspace": workspace_summary,
        "error_code": err,
    }


@endpoint(
    "/invitations/{token}/accept",
    methods=["POST"],
    auth=True,
    tags=["Invitations"],
)
async def post_accept_invitation(
    request: Request,
    token: str,
) -> Dict[str, Any]:
    """Authenticated accept — materializes IS_MEMBER_OF for the calling user."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    user = await get_user_node(user_id)
    if not user:
        raise InsufficientPermissionsError(message="Access denied")
    invitation, err = await consume_invitation_token(token, user.id)
    if err:
        _raise_accept_error(err)

    assert invitation is not None
    workspace = await Workspace.get(invitation.workspace_id)

    return {
        "invitation": _serialize_invitation(invitation),
        "workspace": (
            {"id": workspace.id, "name": workspace.name} if workspace else None
        ),
        "role": invitation.role,
        "message": "Invitation accepted",
    }


@endpoint(
    "/invitations/{token}/decline",
    methods=["POST"],
    auth=True,
    tags=["Invitations"],
)
async def post_decline_invitation(
    request: Request,
    token: str,
) -> Dict[str, Any]:
    """Authenticated decline — caller email must match the invitation."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    user = await get_user_node(user_id)
    if not user:
        raise InsufficientPermissionsError(message="Access denied")

    invitation, err = await decline_invitation(token, user.id)
    if err == ERR_INVALID_TOKEN or not invitation:
        raise ResourceNotFoundError(message="Invitation not found")
    if err == ERR_EMAIL_MISMATCH:
        raise InsufficientPermissionsError(
            message="This invitation was sent to a different email address."
        )
    if err and err != ERR_DECLINED:
        _raise_accept_error(err)

    return {
        "invitation": _serialize_invitation(invitation),
        "message": "Invitation declined",
    }


# ---------------------------------------------------------------------------
# Resource-level invitations (Phase 3)
# ---------------------------------------------------------------------------


_RESOURCE_INVITE_ROLES = {"owner", "admin", "editor", "commenter", "viewer"}


def _validate_resource_invite_role(role: str) -> str:
    normalized = str(role or "").strip().lower()
    if normalized not in _RESOURCE_INVITE_ROLES:
        raise BadRequestError(
            message=f"role must be one of: {', '.join(sorted(_RESOURCE_INVITE_ROLES))}"
        )
    return normalized


async def _post_resource_invitation(
    request: Request,
    resource_type: str,
    resource_id: str,
    email: str,
    role: str,
    message: str,
) -> Dict[str, Any]:
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    if not email or not email.strip():
        raise BadRequestError(message="email is required")
    validated_role = _validate_resource_invite_role(role)

    from app.services.invitations import create_resource_invitation
    from app.services.permissions import resolve_role
    from app.services.sharing import (
        _load_resource,
        _require_true_owner_for_owner_grant,
        _resource_label,
        _resource_workspace_id,
    )

    if (await resolve_role(user_id, resource_type, resource_id)) not in (
        "owner",
        "admin",
    ):
        raise InsufficientPermissionsError(
            message="Only the resource owner or admin may invite collaborators by email."
        )
    await _require_true_owner_for_owner_grant(
        user_id, resource_type, resource_id, validated_role  # type: ignore[arg-type]
    )
    resource = await _load_resource(resource_type, resource_id)  # type: ignore[arg-type]
    if resource is None:
        raise ResourceNotFoundError(message=f"{resource_type.capitalize()} not found")
    workspace_id = await _resource_workspace_id(resource_type, resource) or ""  # type: ignore[arg-type]
    label = await _resource_label(resource_type, resource)  # type: ignore[arg-type]

    user = await get_user_node(user_id)

    try:
        invitation, _plaintext, acceptance_url = await create_resource_invitation(
            resource_type=resource_type,
            resource_id=resource_id,
            workspace_id=workspace_id,
            inviter_user_id=user_id,
            email=email,
            role=validated_role,
            message=message,
            inviter_display_name=getattr(user, "display_name", None) or None,
            resource_label=label,
        )
    except ValueError as exc:
        raise BadRequestError(message=str(exc))

    return {
        "invitation": _serialize_invitation(invitation),
        "acceptance_url": acceptance_url,
        "message": "Invitation created",
    }


@endpoint(
    "/apps/{app_id}/invitations",
    methods=["POST"],
    auth=True,
    tags=["Invitations"],
)
async def post_create_space_invitation(
    request: Request,
    app_id: str,
    email: str = "",
    role: str = "viewer",
    message: str = "",
) -> Dict[str, Any]:
    """Owner-issued resource-level invite for an App (email recipient)."""
    return await _post_resource_invitation(request, "app", app_id, email, role, message)


@endpoint(
    "/tracks/{track_id}/invitations",
    methods=["POST"],
    auth=True,
    tags=["Invitations"],
)
async def post_create_track_invitation(
    request: Request,
    track_id: str,
    email: str = "",
    role: str = "viewer",
    message: str = "",
) -> Dict[str, Any]:
    """Owner-issued resource-level invite for a Track (email recipient)."""
    return await _post_resource_invitation(
        request, "track", track_id, email, role, message
    )


@endpoint(
    "/entries/{entry_id}/invitations",
    methods=["POST"],
    auth=True,
    tags=["Invitations"],
)
async def post_create_entry_invitation(
    request: Request,
    entry_id: str,
    email: str = "",
    role: str = "viewer",
    message: str = "",
) -> Dict[str, Any]:
    """Owner-issued resource-level invite for an Entry (email recipient)."""
    return await _post_resource_invitation(
        request, "entry", entry_id, email, role, message
    )


_ = (export_node, member_edge_bool)
