"""Explicit protected-owner review and reconciliation of pending invitations.

Preview never stamps legacy authority. The review digest binds the complete
stored invitation, current recipient grant and owner's membership lifetime.
Adoption rotates the old token and creates no membership or collaborator.
"""

import hashlib
import json
import secrets
from datetime import datetime
from typing import Any

from jvspatial.core.context import get_default_context

from app.api.errors import InsufficientPermissionsError, ResourceConflictError
from app.exceptions import AuthorizationUnavailableError
from app.middleware.permissions_cache import isolated_permissions_cache
from app.models.edges import INVITED_TO
from app.models.nodes import Invitation
from app.services.authority_facts import primary_node_record, required_node_text
from app.services.invitation_authority import require_workspace_invitation_authority
from app.services.invitation_grants import capture_grant_baseline
from app.services.invitation_lifecycle import invitation_storage_scope
from app.services.invitation_topology import require_invitation_topology
from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource
from app.services.workspace_governance import owner_governance_allowed
from app.services.workspace_permissions import can_access_workspace


async def _review(invitation_id: str, workspace_id: str, actor_id: str) -> dict:
    from app.services.invitations import _is_past, _resolve_user_id_for_email

    if not owner_governance_allowed():
        raise InsufficientPermissionsError(message="Access denied")
    workspace, owner_id, membership_id = await require_workspace_invitation_authority(
        actor_id,
        workspace_id,
        role="member",
        can_create_apps=False,
        can_create_tracks=False,
    )
    with isolated_permissions_cache():
        if await can_access_workspace(owner_id, workspace_id) != "owner":
            raise InsufficientPermissionsError(message="Access denied")
    database = get_default_context().database
    row = await primary_node_record(database, Invitation, invitation_id)
    if row is None:
        raise AuthorizationUnavailableError()
    if required_node_text(row, "status") != "pending":
        raise ResourceConflictError(message="Only pending invitations can be adopted")
    expiry = required_node_text(row, "expires_at")
    try:
        if datetime.fromisoformat(expiry).tzinfo is None:
            raise ValueError("Expiry requires timezone")
    except ValueError as exc:
        raise AuthorizationUnavailableError() from exc
    if _is_past(expiry):
        raise ResourceConflictError(message="Expired invitations must be reissued")
    stored_workspace = required_node_text(row, "workspace_id", allow_empty=True)
    kind = required_node_text(row, "target_resource_type", allow_empty=True)
    target_id = required_node_text(row, "target_resource_id", allow_empty=True)
    if kind:
        if kind not in {"app", "track", "entry"} or not target_id:
            raise AuthorizationUnavailableError()
    elif target_id or not stored_workspace:
        raise AuthorizationUnavailableError()
    location = await locate_resource(
        kind or "workspace",
        target_id or workspace_id,
        allowed_app_states=MANAGEMENT_APP_STATES,
    )
    if location is None or location.workspace_id != workspace_id:
        raise InsufficientPermissionsError(message="Access denied")
    if stored_workspace and stored_workspace != workspace_id:
        raise AuthorizationUnavailableError()
    legacy_resource_only = bool(kind and not stored_workspace)
    await require_invitation_topology(
        invitation_id,
        workspace_id,
        kind,
        target_id,
        legacy_resource_only=legacy_resource_only,
    )
    revision = row["context"].get("invitation_revision", 0)
    if type(revision) is not int or revision < 0:
        raise AuthorizationUnavailableError()
    email = required_node_text(row, "email")
    recipient_id = await _resolve_user_id_for_email(email)
    baseline = await capture_grant_baseline(
        recipient_id, target_id or workspace_id, workspace_membership=not bool(kind)
    )
    material: dict[str, Any] = {
        "record": row,
        "workspace_id": workspace_id,
        "owner_id": owner_id,
        "owner_membership_id": membership_id,
        "recipient_id": recipient_id,
        "grant_baseline": baseline,
    }
    # Taking the row lock introduces only this technical zero on old records.
    # Normalize it in the review hash, without supplying any authority field.
    material["record"] = dict(row)
    material["record"]["context"] = dict(row["context"])
    material["record"]["context"].setdefault("invitation_revision", 0)
    digest = hashlib.sha256(
        json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()
    return {
        "review_digest": digest,
        "invitation_id": invitation_id,
        "workspace_id": workspace_id,
        "email": email,
        "expires_at": expiry,
        "target_resource_type": kind or None,
        "target_resource_id": target_id or None,
        "requested_role": row["context"].get(
            "target_resource_role" if kind else "role"
        ),
        "requested_can_create_apps": row["context"].get("can_create_apps"),
        "requested_can_create_tracks": row["context"].get("can_create_tracks"),
        "original_issuer_id": required_node_text(row, "invited_by_user_id"),
        "owner_id": owner_id,
        "owner_membership_id": membership_id,
        "recipient_id": recipient_id,
        "grant_baseline": baseline,
        "revision": revision,
        "legacy_resource_only": legacy_resource_only,
    }


async def preview_invitation_adoption(
    invitation_id: str, workspace_id: str, *, actor_id: str
) -> dict:
    """Return a reviewable current snapshot without changing the record."""
    return await _review(invitation_id, workspace_id, actor_id)


async def adopt_invitation(
    invitation_id: str,
    workspace_id: str,
    *,
    actor_id: str,
    review_digest: str,
    role: str,
    can_create_apps: bool,
    can_create_tracks: bool,
) -> tuple[Invitation, str, str]:
    """Adopt reviewed pending intent with new owner provenance and a fresh token."""
    from app.services.invitation_authority import require_resource_invitation_authority
    from app.services.invitations import (
        _build_acceptance_url,
        _deliver_invitation_audit,
        _emit_invitation_change,
        _generate_token,
        _invitation_snapshot,
        _now_utc,
    )

    if (
        not isinstance(review_digest, str)
        or len(review_digest) != 64
        or type(can_create_apps) is not bool
        or type(can_create_tracks) is not bool
    ):
        raise ValueError("invitation.invalid_adoption_review")
    caller_context = get_default_context()
    async with invitation_storage_scope(
        invitation_id, workspace_id, legacy_review=True
    ):
        review = await _review(invitation_id, workspace_id, actor_id)
        if not secrets.compare_digest(review_digest, review["review_digest"]):
            raise ResourceConflictError(
                message="Invitation or access changed; review again"
            )
        kind = review["target_resource_type"]
        if kind:
            if can_create_apps or can_create_tracks:
                raise ValueError("Resource invitations cannot grant creation rights")
            await require_resource_invitation_authority(
                review["owner_id"],
                kind,
                review["target_resource_id"],
                workspace_id,
                role=role,
            )
        else:
            await require_workspace_invitation_authority(
                review["owner_id"],
                workspace_id,
                role=role,
                can_create_apps=can_create_apps,
                can_create_tracks=can_create_tracks,
            )
        invitation = await Invitation.find_one({"id": invitation_id})
        if invitation is None:
            raise AuthorizationUnavailableError()
        before = _invitation_snapshot(invitation)
        now = _now_utc().isoformat()
        token, token_hash = _generate_token()
        invitation.workspace_id = workspace_id
        invitation.adopted_by_user_id = review["owner_id"]
        invitation.adopted_membership_id = review["owner_membership_id"]
        invitation.adopted_at = now
        invitation.invited_user_id = review["recipient_id"]
        invitation.grant_baseline = review["grant_baseline"]
        invitation.invitation_revision = review["revision"] + 1
        invitation.token_hash = token_hash
        invitation.role = "guest" if kind else role
        invitation.target_resource_role = role if kind else ""
        invitation.can_create_apps = can_create_apps
        invitation.can_create_tracks = can_create_tracks
        await invitation.save()
        if review["legacy_resource_only"]:
            from app.models.nodes import Workspace

            workspace = await Workspace.find_one({"id": workspace_id})
            await invitation.connect(workspace, edge=INVITED_TO, issued_at=now)
        await _emit_invitation_change(
            actor_user_id=review["owner_id"],
            action="workspace.invitation_adopt",
            invitation=invitation,
            before=before,
            scope=f"ws:{workspace_id}",
        )
    await invitation.set_context(caller_context)
    await _deliver_invitation_audit(invitation)
    return invitation, token, _build_acceptance_url(token)
