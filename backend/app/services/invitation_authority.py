"""Current service-boundary authority for invitation issuance.

Creation rights permit creating resources, not delegating those rights. Until
explicit delegation grants are supported, only protected workspace ownership
can issue administrator membership or creation flags.
"""

from app.api.errors import InsufficientPermissionsError
from app.exceptions import AuthorizationUnavailableError
from app.middleware.permissions_cache import isolated_permissions_cache
from app.models.nodes import Invitation, User, Workspace
from app.services.permissions import get_user_node
from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource
from app.services.sharing import VALID_ROLES
from app.services.workspace_governance import owner_governance_allowed
from app.services.workspace_kind import is_collaborative_kind
from app.services.workspace_permissions import can_access_workspace, read_member_facts


async def _issuer_membership_id(issuer: User, workspace_id: str) -> str:
    """Capture the unique current membership identity, never a hydrated default."""
    row = await read_member_facts(issuer, workspace_id)
    if row is None:
        raise InsufficientPermissionsError(message="Access denied")
    identity = row.get("id")
    if not isinstance(identity, str) or not identity:
        raise AuthorizationUnavailableError()
    return identity


async def require_workspace_invitation_authority(
    issuer_id: str,
    workspace_id: str,
    *,
    role: str,
    can_create_apps: bool,
    can_create_tracks: bool,
) -> tuple[Workspace, str, str]:
    """Validate the exact current target and requested delegation ceiling."""
    if role not in {"admin", "member", "guest"}:
        raise ValueError("invitation.invalid_role")
    if type(can_create_apps) is not bool or type(can_create_tracks) is not bool:
        raise ValueError("invitation.invalid_creation_flags")
    with isolated_permissions_cache():
        issuer = await get_user_node(issuer_id)
        location = await locate_resource(
            "workspace", workspace_id, allowed_app_states=MANAGEMENT_APP_STATES
        )
        workspace = await Workspace.find_one({"id": workspace_id})
        if (
            issuer is None
            or location is None
            or workspace is None
            or not is_collaborative_kind(workspace.kind)
        ):
            raise InsufficientPermissionsError(message="Access denied")
        current_role = await can_access_workspace(issuer.id, workspace_id)
        protected_owner = current_role == "owner" and owner_governance_allowed()
        if not protected_owner and current_role != "admin":
            raise InsufficientPermissionsError(message="Access denied")
        if not protected_owner and (
            role == "admin" or can_create_apps or can_create_tracks
        ):
            raise InsufficientPermissionsError(
                message="Only the workspace owner can delegate administrator or creation rights"
            )
        return workspace, issuer.id, await _issuer_membership_id(issuer, workspace_id)


async def require_resource_invitation_authority(
    issuer_id: str,
    resource_type: str,
    resource_id: str,
    workspace_id: str,
    *,
    role: str,
) -> tuple[str, str, str]:
    """Require a current issuer policy decision on the rooted exact target."""
    from app.services.sharing import (
        _require_share_authority,
        _require_true_owner_for_owner_grant,
    )

    if resource_type not in {"app", "track", "entry"}:
        raise ValueError("invitation.invalid_resource_type")
    if role not in VALID_ROLES:
        raise ValueError("invitation.invalid_role")
    with isolated_permissions_cache():
        issuer = await get_user_node(issuer_id)
        location = await locate_resource(
            resource_type, resource_id, allowed_app_states=MANAGEMENT_APP_STATES
        )
        if (
            issuer is None
            or location is None
            or (workspace_id and location.workspace_id != workspace_id)
        ):
            raise InsufficientPermissionsError(message="Access denied")
        await _require_share_authority(
            issuer.id,
            resource_type,  # type: ignore[arg-type]
            resource_id,
            policy_action=f"{resource_type}.invitation_create",
        )
        await _require_true_owner_for_owner_grant(
            issuer.id,
            resource_type,  # type: ignore[arg-type]
            resource_id,
            role,
        )
        return (
            location.workspace_id,
            issuer.id,
            await _issuer_membership_id(issuer, location.workspace_id),
        )


async def require_current_invitation_issuer(invitation: Invitation) -> None:
    """Reapply issuance authority before grants; this is not a commit fence."""
    issuer_id = invitation.invited_by_user_id
    bound_membership = invitation.issuer_membership_id
    if invitation.adopted_by_user_id:
        issuer_id = invitation.adopted_by_user_id
        bound_membership = invitation.adopted_membership_id
        with isolated_permissions_cache():
            if (
                not owner_governance_allowed()
                or await can_access_workspace(issuer_id, invitation.workspace_id)
                != "owner"
            ):
                raise InsufficientPermissionsError(message="Access denied")
    if invitation.target_resource_type or invitation.target_resource_id:
        if not invitation.workspace_id:
            raise InsufficientPermissionsError(message="Access denied")
        _, _, membership_id = await require_resource_invitation_authority(
            issuer_id,
            invitation.target_resource_type,
            invitation.target_resource_id,
            invitation.workspace_id,
            role=invitation.target_resource_role,
        )
    else:
        _, _, membership_id = await require_workspace_invitation_authority(
            issuer_id,
            invitation.workspace_id,
            role=invitation.role,
            can_create_apps=invitation.can_create_apps,
            can_create_tracks=invitation.can_create_tracks,
        )
    if not bound_membership or bound_membership != membership_id:
        # Legacy pending records need explicit owner adoption/reissuance.
        # Reading today's membership cannot establish the historical issuer.
        raise InsufficientPermissionsError(message="Access denied")


async def require_invitation_revoke_authority(
    invitation: Invitation, actor_id: str
) -> None:
    """Authorize cancellation from current state, including service-only callers."""
    from app.services.sharing import _require_share_authority

    with isolated_permissions_cache():
        actor = await get_user_node(actor_id)
        if actor is None:
            raise InsufficientPermissionsError(message="Access denied")
        await _issuer_membership_id(actor, invitation.workspace_id)
        kind = invitation.target_resource_type or "workspace"
        target_id = invitation.target_resource_id or invitation.workspace_id
        location = await locate_resource(
            kind, target_id, allowed_app_states=MANAGEMENT_APP_STATES
        )
        if location is None or location.workspace_id != invitation.workspace_id:
            raise InsufficientPermissionsError(message="Access denied")
        if invitation.invited_by_user_id in {actor.id, actor.user_id}:
            return
        if kind == "workspace":
            role = await can_access_workspace(actor.id, invitation.workspace_id)
            if role == "admin" or (role == "owner" and owner_governance_allowed()):
                return
            raise InsufficientPermissionsError(message="Access denied")
        await _require_share_authority(
            actor.id,
            kind,  # type: ignore[arg-type]
            target_id,
            policy_action=f"{kind}.invitation_revoke",
        )
