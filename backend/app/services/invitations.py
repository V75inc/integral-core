"""Workspace invitation flow.

Token-based invitations to join an organization-kind Workspace. Plaintext
token is generated once and returned to the issuer (in the API response) —
only its SHA-256 hash is persisted. Acceptance materializes
``IS_MEMBER_OF{role:role}`` between the accepting User and the target
Workspace.

Public surface:
    create_invitation(...) -> (Invitation, plaintext_token, acceptance_url)
    preview_invitation(token) -> (Invitation|None, err)
    consume_invitation_token(token, accepting_user_id) -> (Invitation|None, err)
    decline_invitation(token, declining_user_id) -> (Invitation|None, err)
    revoke_invitation(invitation, *, actor_user_id) -> None
    expire_invitations() -> int   # background sweep
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Tuple

from app.bootstrap_admin import _find_user_by_email
from app.config import settings
from app.models.edges import INVITED_TO, IS_MEMBER_OF
from app.models.nodes import Invitation, User, Workspace
from app.services.app_graph import catalog_invitation
from app.services.email_service import render_invitation_email, send_email

logger = logging.getLogger(__name__)

ERR_INVALID_TOKEN = "invitation.token_invalid"
ERR_EXPIRED = "invitation.token_expired"
ERR_REVOKED = "invitation.revoked"
ERR_DECLINED = "invitation.declined"
ERR_CONSUMED = "invitation.consumed"
ERR_ALREADY_MEMBER = "invitation.already_member"
ERR_DUPLICATE_PENDING = "invitation.duplicate_pending"
ERR_EMAIL_INVALID = "invitation.email_invalid"
ERR_USER_NOT_FOUND = "invitation.user_not_found"
ERR_WORKSPACE_NOT_FOUND = "invitation.workspace_not_found"
ERR_EMAIL_MISMATCH = "invitation.email_mismatch"
ERR_GRANT_FAILED = "invitation.grant_failed"
ERR_RESOURCE_NOT_FOUND = "invitation.resource_not_found"
ERR_NOT_FOUND = "invitation.not_found"


def _invitation_snapshot(invitation: Invitation) -> dict:
    """Minimal audit payload (avoid importing API serializers)."""
    return {
        "id": invitation.id,
        "workspace_id": invitation.workspace_id,
        "email": invitation.email,
        "status": invitation.status,
        "role": invitation.role,
        "target_resource_type": invitation.target_resource_type or None,
        "target_resource_id": invitation.target_resource_id or None,
        "target_resource_role": invitation.target_resource_role or None,
    }


async def _emit_invitation_change(
    *,
    actor_user_id: str,
    action: str,
    invitation: Invitation,
    before: Optional[dict] = None,
    scope: Optional[str] = None,
) -> None:
    from typing import cast

    from app.schemas.audit import ChangeEventAction
    from app.services.change_event import emit_change_event

    resource_type = "Workspace"
    resource_id = invitation.workspace_id or ""
    if invitation.target_resource_type and invitation.target_resource_id:
        resource_type = invitation.target_resource_type.capitalize()
        resource_id = invitation.target_resource_id
    await emit_change_event(
        actor_kind="human",
        actor_id=actor_user_id,
        action=cast(ChangeEventAction, action),
        resource_type=resource_type,
        resource_id=resource_id,
        before=before,
        after=_invitation_snapshot(invitation),
        scope=scope or f"user:{actor_user_id}",
    )


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _expiry_iso(days: int) -> str:
    return (_now_utc() + timedelta(days=days)).isoformat()


def _is_past(iso: Optional[str]) -> bool:
    if not iso:
        return False
    try:
        ts = datetime.fromisoformat(iso)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True
    return _now_utc() >= ts


def _generate_token() -> Tuple[str, str]:
    plaintext = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    return plaintext, token_hash


def _build_acceptance_url(plaintext_token: str) -> str:
    base = settings.APP_BASE_URL.rstrip("/")
    return f"{base}/invitations/{plaintext_token}"


async def _normalize_email(email: str) -> str:
    from app.api.validators_common import validate_email

    return validate_email(email)


async def _auth_email_for_graph_user(user: User) -> Optional[str]:
    """Return normalized AuthUser email for a graph ``User``, if linked."""
    auth_id = (getattr(user, "user_id", None) or "").strip()
    if not auth_id:
        return None
    try:
        from jvspatial.api.auth.models import User as AuthUser

        auth_user = await AuthUser.get(auth_id)
        if not auth_user:
            return None
        raw = (getattr(auth_user, "email", None) or "").strip()
        if not raw:
            return None
        return await _normalize_email(raw)
    except Exception:
        return None


async def _invitee_email_matches_user(invitation: Invitation, user: User) -> bool:
    """True when the signed-in principal's email matches the invitation."""
    try:
        invite_email = await _normalize_email(invitation.email or "")
    except Exception:
        return False
    auth_email = await _auth_email_for_graph_user(user)
    if not auth_email:
        return False
    return secrets.compare_digest(invite_email, auth_email)


async def _wire_invitation_edges(
    invitation: Invitation,
    *,
    workspace: Optional[Workspace] = None,
    resource: Any = None,
    issued_at: str,
) -> None:
    """Attach INVITED_TO edges for workspace and/or resource targets (I-GRAPH-01)."""
    if workspace is not None:
        await invitation.connect(workspace, edge=INVITED_TO, issued_at=issued_at)
    if resource is not None:
        await invitation.connect(resource, edge=INVITED_TO, issued_at=issued_at)


async def _resolve_user_id_for_email(email: str) -> Optional[str]:
    auth_user = await _find_user_by_email(email)
    if not auth_user:
        return None
    matches = await User.find({"context.user_id": auth_user.id})
    if matches:
        return matches[0].id
    return None


async def _emit_invitation_in_app_notification(
    *,
    invitation: Invitation,
    invited_user_id: str,
    inviter_user_id: str,
    inviter_display_name: Optional[str],
    target_label: str,
    role: str,
) -> None:
    """Best-effort in-app notification for a resolved registered invitee.

    Email delivery for workspace invites is handled separately by
    ``create_invitation`` / ``create_resource_invitation``; this path
    fires in-app only so notification preferences do not double-send email.
    """
    if not invited_user_id:
        return
    try:
        from app.services import notification_router

        actor = (inviter_display_name or "").strip() or "Someone"
        summary_role = role or "member"
        await notification_router.dispatch(
            user_id=invited_user_id,
            kind="invitation",
            payload={
                "invitation_id": invitation.id,
                "workspace_id": invitation.workspace_id or "",
                "actor_name": actor,
                "role": summary_role,
                "target_label": target_label,
                "content": (f"{actor} invited you to {target_label} as {summary_role}"),
            },
            actor_id=inviter_user_id,
            actor_kind="human",
            channels=["in_app"],
            idempotency_key=f"invitation:{invitation.id}:{invited_user_id}",
        )
    except Exception:
        logger.exception(
            "failed to dispatch in-app invitation notification for %s",
            invitation.id,
        )


async def _existing_membership(workspace_id: str, user_id: str) -> bool:
    user = await User.get(user_id)
    if not user:
        return False
    ctx = await user.get_context()
    edges = await ctx.find_edges_between(
        source_id=user.id, target_id=workspace_id, edge_class=IS_MEMBER_OF
    )
    return bool(edges)


async def create_invitation(
    *,
    workspace: Workspace,
    inviter_user_id: str,
    email: str,
    role: str = "member",
    can_create_apps: bool = False,
    can_create_tracks: bool = False,
    message: str = "",
    send_email_notification: bool = True,
    inviter_display_name: Optional[str] = None,
) -> Tuple[Invitation, str, str]:
    """Create a pending Invitation and return ``(invitation, token, url)``.

    Caller is responsible for the role enum check (the API layer rejects
    invalid roles before reaching the service).

    Raises ValueError with one of the ERR_* codes for create-time failures.
    """
    from app.api.validators_common import validate_email

    try:
        normalized_email = validate_email(email)
    except Exception:
        raise ValueError("invitation.email_invalid")

    invited_user_id = await _resolve_user_id_for_email(normalized_email)
    if invited_user_id and await _existing_membership(workspace.id, invited_user_id):
        raise ValueError(ERR_ALREADY_MEMBER)

    # Reject duplicate pending invitations to the same email for the same workspace.
    pending = await Invitation.find(
        {
            "context.workspace_id": workspace.id,
            "context.email": normalized_email,
            "context.status": "pending",
        }
    )
    if pending:
        raise ValueError(ERR_DUPLICATE_PENDING)

    plaintext, token_hash = _generate_token()
    now_iso = _now_utc().isoformat()
    invitation = await Invitation.create(
        workspace_id=workspace.id,
        email=normalized_email,
        invited_user_id=invited_user_id,
        invited_by_user_id=inviter_user_id,
        role=role,
        can_create_apps=can_create_apps,
        can_create_tracks=can_create_tracks,
        token_hash=token_hash,
        status="pending",
        message=message or "",
        created_at=now_iso,
        expires_at=_expiry_iso(settings.INVITATION_EXPIRY_DAYS),
    )
    await _wire_invitation_edges(invitation, workspace=workspace, issued_at=now_iso)
    try:
        await catalog_invitation(invitation)
    except Exception:
        logger.exception("catalog_invitation failed for %s", invitation.id)

    acceptance_url = _build_acceptance_url(plaintext)

    if send_email_notification:
        with contextlib.suppress(Exception):
            msg = render_invitation_email(
                recipient_email=email.strip() or normalized_email,
                inviter_name=inviter_display_name,
                organization_name=workspace.name or "a workspace",
                role=role,
                acceptance_url=acceptance_url,
                message=message,
                expires_days=settings.INVITATION_EXPIRY_DAYS,
            )
            await send_email(msg)

    if invited_user_id:
        await _emit_invitation_in_app_notification(
            invitation=invitation,
            invited_user_id=invited_user_id,
            inviter_user_id=inviter_user_id,
            inviter_display_name=inviter_display_name,
            target_label=workspace.name or "a workspace",
            role=role,
        )

    await _emit_invitation_change(
        actor_user_id=inviter_user_id,
        action="workspace.invitation_create",
        invitation=invitation,
        scope=f"user:{inviter_user_id}",
    )
    return invitation, plaintext, acceptance_url


async def _find_invitation_by_token(plaintext: str) -> Optional[Invitation]:
    if not plaintext:
        return None
    token_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    matches = await Invitation.find({"context.token_hash": token_hash})
    if matches:
        candidate = matches[0]
        if secrets.compare_digest(str(candidate.token_hash or ""), token_hash):
            return candidate
    return None


async def preview_invitation(
    plaintext: str,
) -> Tuple[Optional[Invitation], Optional[str]]:
    """Return ``(invitation, error_code)`` for an unauthenticated preview."""
    invitation = await _find_invitation_by_token(plaintext)
    if not invitation:
        return None, ERR_INVALID_TOKEN
    if invitation.status == "revoked":
        return invitation, ERR_REVOKED
    if invitation.status == "declined":
        return invitation, ERR_DECLINED
    if invitation.status == "accepted":
        return invitation, ERR_CONSUMED
    if invitation.status == "expired" or _is_past(invitation.expires_at):
        return invitation, ERR_EXPIRED
    return invitation, None


async def _materialize_invitation_grants(
    user: User, invitation: Invitation
) -> Optional[str]:
    """Create membership/collaboration edges. Returns an ERR_* code on failure."""
    is_resource_invite = bool(
        invitation.target_resource_type and invitation.target_resource_id
    )

    if invitation.workspace_id:
        ws = await Workspace.get(invitation.workspace_id)
        if ws is None and not is_resource_invite:
            return ERR_WORKSPACE_NOT_FOUND
        if ws is not None and not is_resource_invite:
            from app.services.edge_upsert import ensure_edge

            invite_role = invitation.role or "member"
            can_apps = bool(invitation.can_create_apps) or invite_role == "admin"
            can_tracks = bool(invitation.can_create_tracks) or invite_role == "admin"
            await ensure_edge(
                user,
                ws,
                IS_MEMBER_OF,
                role=invite_role,
                joined_at=_now_utc().isoformat(),
                can_create_apps=can_apps,
                can_create_tracks=can_tracks,
            )
    elif not is_resource_invite:
        return ERR_WORKSPACE_NOT_FOUND

    if not is_resource_invite:
        return None

    from app.models.edges import COLLABORATES_ON
    from app.services.sharing import _load_resource, ensure_guest_membership

    resource = await _load_resource(
        invitation.target_resource_type,  # type: ignore[arg-type]
        invitation.target_resource_id,
    )
    if resource is None:
        return ERR_RESOURCE_NOT_FOUND

    if invitation.workspace_id:
        await ensure_guest_membership(
            user,
            invitation.workspace_id,
            inviter_user_id=invitation.invited_by_user_id,
            source_label=(
                f"invitation:{invitation.id}:"
                f"{invitation.target_resource_type}:"
                f"{invitation.target_resource_id}"
            ),
        )
    role = invitation.target_resource_role or "viewer"
    from app.services.edge_upsert import ensure_edge

    await ensure_edge(
        user,
        resource,
        COLLABORATES_ON,
        role=role,
        invited_at=_now_utc().isoformat(),
        invited_by=invitation.invited_by_user_id or "system:invitation",
    )
    return None


async def _finalize_invitation_acceptance(
    *,
    invitation: Invitation,
    user: User,
    accepting_user_id: str,
) -> Tuple[Optional[Invitation], Optional[str]]:
    """Materialize grants and mark a pending invitation accepted."""
    try:
        grant_err = await _materialize_invitation_grants(user, invitation)
    except Exception:
        logger.exception(
            "failed to materialize grants for invitation %s", invitation.id
        )
        return invitation, ERR_GRANT_FAILED
    if grant_err:
        return invitation, grant_err

    prior = _invitation_snapshot(invitation)
    invitation.status = "accepted"
    invitation.consumed_at = _now_utc().isoformat()
    invitation.invited_user_id = user.id
    await invitation.save()

    action = (
        f"{invitation.target_resource_type}.invitation_accept"
        if invitation.target_resource_type
        else "workspace.invitation_accept"
    )
    await _emit_invitation_change(
        actor_user_id=accepting_user_id,
        action=action,
        invitation=invitation,
        before=prior,
        scope=f"user:{accepting_user_id}",
    )
    return invitation, None


async def consume_invitation_token(
    plaintext: str, accepting_user_id: str
) -> Tuple[Optional[Invitation], Optional[str]]:
    """Accept the invitation: create IS_MEMBER_OF and mark consumed.

    Returns ``(invitation, error_code)``. Idempotent on a previously-accepted
    invite by the same user.
    """
    invitation, err = await preview_invitation(plaintext)
    if err and err != ERR_CONSUMED:
        return invitation, err
    if not invitation:
        return None, ERR_INVALID_TOKEN
    if err == ERR_CONSUMED:
        if (
            invitation.invited_user_id
            and invitation.invited_user_id != accepting_user_id
        ):
            return invitation, ERR_CONSUMED
        return invitation, None

    user = await User.get(accepting_user_id)
    if not user:
        return invitation, ERR_USER_NOT_FOUND

    if not await _invitee_email_matches_user(invitation, user):
        return invitation, ERR_EMAIL_MISMATCH

    return await _finalize_invitation_acceptance(
        invitation=invitation,
        user=user,
        accepting_user_id=accepting_user_id,
    )


async def accept_invitation_by_id(
    invitation_id: str, accepting_user_id: str
) -> Tuple[Optional[Invitation], Optional[str]]:
    """Accept a pending invitation by id for the authenticated invitee."""
    invitation = await Invitation.get(invitation_id)
    if not invitation:
        return None, ERR_NOT_FOUND
    if invitation.status == "accepted":
        if (
            invitation.invited_user_id
            and invitation.invited_user_id != accepting_user_id
        ):
            return invitation, ERR_CONSUMED
        return invitation, None
    preview_err: Optional[str] = None
    if invitation.status == "revoked":
        preview_err = ERR_REVOKED
    elif invitation.status == "declined":
        preview_err = ERR_DECLINED
    elif invitation.status == "expired" or _is_past(invitation.expires_at):
        preview_err = ERR_EXPIRED
    elif invitation.status != "pending":
        preview_err = ERR_INVALID_TOKEN
    if preview_err:
        return invitation, preview_err

    user = await User.get(accepting_user_id)
    if not user:
        return invitation, ERR_USER_NOT_FOUND
    if not await _invitee_email_matches_user(invitation, user):
        return invitation, ERR_EMAIL_MISMATCH

    return await _finalize_invitation_acceptance(
        invitation=invitation,
        user=user,
        accepting_user_id=accepting_user_id,
    )


async def create_resource_invitation(
    *,
    resource_type: str,
    resource_id: str,
    workspace_id: str,
    inviter_user_id: str,
    email: str,
    role: str,
    message: str = "",
    send_email_notification: bool = True,
    inviter_display_name: Optional[str] = None,
    resource_label: str = "",
) -> Tuple[Invitation, str, str]:
    """Create an Invitation that grants resource-level access on accept.

    Recipient may be a non-Integral user; the acceptance flow drives them
    to sign-up first if needed. On accept, ``consume_invitation_token``
    materializes both ``COLLABORATES_ON`` on the resource and (when the
    recipient isn't a workspace member yet) ``IS_MEMBER_OF{role:"guest"}``.
    """
    try:
        normalized_email = await _normalize_email(email)
    except Exception:
        raise ValueError(ERR_EMAIL_INVALID) from None
    if resource_type not in ("app", "track", "entry"):
        raise ValueError("invitation.invalid_resource_type")
    if role not in ("owner", "admin", "editor", "commenter", "viewer"):
        raise ValueError("invitation.invalid_role")

    from app.services.sharing import _require_true_owner_for_owner_grant

    await _require_true_owner_for_owner_grant(
        inviter_user_id,
        resource_type,  # type: ignore[arg-type]
        resource_id,
        role,
    )

    invited_user_id = await _resolve_user_id_for_email(normalized_email)

    pending = await Invitation.find(
        {
            "context.target_resource_type": resource_type,
            "context.target_resource_id": resource_id,
            "context.email": normalized_email,
            "context.status": "pending",
        }
    )
    if pending:
        raise ValueError(ERR_DUPLICATE_PENDING)

    plaintext, token_hash = _generate_token()
    now_iso = _now_utc().isoformat()
    invitation = await Invitation.create(
        workspace_id=workspace_id or "",
        email=normalized_email,
        invited_user_id=invited_user_id,
        invited_by_user_id=inviter_user_id,
        role="guest",  # workspace-level placeholder; resource-level fields drive the grant
        can_create_apps=False,
        can_create_tracks=False,
        target_resource_type=resource_type,
        target_resource_id=resource_id,
        target_resource_role=role,
        token_hash=token_hash,
        status="pending",
        message=message or "",
        created_at=now_iso,
        expires_at=_expiry_iso(settings.INVITATION_EXPIRY_DAYS),
    )
    from app.services.sharing import _load_resource

    resource = await _load_resource(resource_type, resource_id)  # type: ignore[arg-type]
    ws = await Workspace.get(workspace_id) if workspace_id else None
    await _wire_invitation_edges(
        invitation,
        workspace=ws,
        resource=resource,
        issued_at=now_iso,
    )
    try:
        await catalog_invitation(invitation)
    except Exception:
        logger.exception("catalog_invitation failed for %s", invitation.id)

    acceptance_url = _build_acceptance_url(plaintext)

    if send_email_notification:
        with contextlib.suppress(Exception):
            msg = render_invitation_email(
                recipient_email=email.strip() or normalized_email,
                inviter_name=inviter_display_name,
                organization_name=resource_label
                or f"{resource_type.capitalize()} (Integral)",
                role=role,
                acceptance_url=acceptance_url,
                message=message,
                expires_days=settings.INVITATION_EXPIRY_DAYS,
            )
            await send_email(msg)

    if invited_user_id:
        await _emit_invitation_in_app_notification(
            invitation=invitation,
            invited_user_id=invited_user_id,
            inviter_user_id=inviter_user_id,
            inviter_display_name=inviter_display_name,
            target_label=resource_label or f"{resource_type.capitalize()} (Integral)",
            role=role,
        )

    await _emit_invitation_change(
        actor_user_id=inviter_user_id,
        action=f"{resource_type}.invitation_create",
        invitation=invitation,
        scope=f"{resource_type}:{resource_id}",
    )
    return invitation, plaintext, acceptance_url


async def _finalize_invitation_decline(
    *,
    invitation: Invitation,
    user: User,
    declining_user_id: str,
) -> Tuple[Invitation, None]:
    prior = _invitation_snapshot(invitation)
    invitation.status = "declined"
    invitation.consumed_at = _now_utc().isoformat()
    invitation.invited_user_id = user.id
    await invitation.save()
    action = (
        f"{invitation.target_resource_type}.invitation_decline"
        if invitation.target_resource_type
        else "workspace.invitation_decline"
    )
    await _emit_invitation_change(
        actor_user_id=declining_user_id,
        action=action,
        invitation=invitation,
        before=prior,
        scope=f"user:{declining_user_id}",
    )
    return invitation, None


async def decline_invitation(
    plaintext: str,
    declining_user_id: str,
) -> Tuple[Optional[Invitation], Optional[str]]:
    """Mark an invitation as declined; idempotent on terminal states.

    Requires an authenticated principal whose email matches the invitation.
    """
    invitation, err = await preview_invitation(plaintext)
    if not invitation:
        return None, err or ERR_INVALID_TOKEN
    if err in (ERR_REVOKED, ERR_DECLINED, ERR_CONSUMED, ERR_EXPIRED):
        return invitation, err

    user = await User.get(declining_user_id)
    if not user:
        return invitation, ERR_USER_NOT_FOUND
    if not await _invitee_email_matches_user(invitation, user):
        return invitation, ERR_EMAIL_MISMATCH

    return await _finalize_invitation_decline(
        invitation=invitation,
        user=user,
        declining_user_id=declining_user_id,
    )


async def decline_invitation_by_id(
    invitation_id: str,
    declining_user_id: str,
) -> Tuple[Optional[Invitation], Optional[str]]:
    """Decline a pending invitation by id for the authenticated invitee."""
    invitation = await Invitation.get(invitation_id)
    if not invitation:
        return None, ERR_NOT_FOUND
    if invitation.status == "declined":
        return invitation, ERR_DECLINED
    if invitation.status in ("revoked", "accepted", "expired"):
        return invitation, {
            "revoked": ERR_REVOKED,
            "accepted": ERR_CONSUMED,
            "expired": ERR_EXPIRED,
        }.get(invitation.status, ERR_INVALID_TOKEN)
    if invitation.status != "pending":
        return invitation, ERR_INVALID_TOKEN
    if _is_past(invitation.expires_at):
        return invitation, ERR_EXPIRED

    user = await User.get(declining_user_id)
    if not user:
        return invitation, ERR_USER_NOT_FOUND
    if not await _invitee_email_matches_user(invitation, user):
        return invitation, ERR_EMAIL_MISMATCH

    return await _finalize_invitation_decline(
        invitation=invitation,
        user=user,
        declining_user_id=declining_user_id,
    )


async def revoke_invitation(invitation: Invitation, *, actor_user_id: str) -> None:
    """Revoke a pending invitation (irreversible)."""
    prior = _invitation_snapshot(invitation)
    invitation.status = "revoked"
    invitation.consumed_at = _now_utc().isoformat()
    await invitation.save()
    action = (
        f"{invitation.target_resource_type}.invitation_revoke"
        if invitation.target_resource_type
        else "workspace.invitation_revoke"
    )
    scope = (
        f"{invitation.target_resource_type}:{invitation.target_resource_id}"
        if invitation.target_resource_type and invitation.target_resource_id
        else f"user:{actor_user_id}"
    )
    await _emit_invitation_change(
        actor_user_id=actor_user_id,
        action=action,
        invitation=invitation,
        before=prior,
        scope=scope,
    )


async def expire_invitations(*, batch_limit: int = 500) -> int:
    """Background sweep: mark past-expiry pending invitations as expired.

    Scans pending rows only (status-indexed). ``batch_limit`` caps work per
    scheduler tick so a large backlog cannot block the event loop.
    """
    count = 0
    pending = await Invitation.find({"context.status": "pending"})
    now_iso = _now_utc().isoformat()
    for inv in pending:
        if count >= batch_limit:
            break
        if not _is_past(inv.expires_at):
            continue
        inv.status = "expired"
        inv.consumed_at = now_iso
        await inv.save()
        count += 1
    return count
