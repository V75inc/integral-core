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
from functools import wraps
from typing import Any, Awaitable, Callable, Optional, ParamSpec, Tuple

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
ERR_EMAIL_UNVERIFIED = "invitation.email_unverified"
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
        "invitation_revision": invitation.invitation_revision,
        "grant_outcome": invitation.grant_outcome,
        "granted_role": invitation.granted_role,
        "granted_can_create_apps": invitation.granted_can_create_apps,
        "granted_can_create_tracks": invitation.granted_can_create_tracks,
        "original_issuer_id": invitation.invited_by_user_id,
        "adopted_by_user_id": invitation.adopted_by_user_id,
        "adopted_at": invitation.adopted_at,
    }


async def _emit_invitation_change(
    *,
    actor_user_id: str,
    action: str,
    invitation: Invitation,
    before: Optional[dict] = None,
    scope: Optional[str] = None,
    system_actor: bool = False,
) -> None:
    from typing import cast

    from app.schemas.audit import ChangeEventAction
    from app.services.change_event import emit_change_event

    resource_type = "Workspace"
    resource_id = invitation.workspace_id or ""
    if invitation.target_resource_type and invitation.target_resource_id:
        resource_type = invitation.target_resource_type.capitalize()
        resource_id = invitation.target_resource_id
    event = {
        "actor_kind": "system" if system_actor else "human",
        "actor_id": actor_user_id,
        "action": cast(ChangeEventAction, action),
        "resource_type": resource_type,
        "resource_id": resource_id,
        "before": before,
        "after": _invitation_snapshot(invitation),
        "scope": scope or f"user:{actor_user_id}",
    }
    from jvspatial.core.context import get_default_context

    from app.services.entry_write_scope import is_postgres_entry_transaction

    database = get_default_context().database
    if is_postgres_entry_transaction(database):
        from app.services.app_operations.event_outbox import insert_revision_event

        invitation.audit_outbox_id = await insert_revision_event(
            transaction=database,
            workspace_id=invitation.workspace_id,
            namespace="invitation-lifecycle-v1",
            subject_id=invitation.id,
            revision=invitation.invitation_revision,
            event=event,
        )
        await invitation.save()
    else:
        await emit_change_event(**event)


async def _deliver_invitation_audit(invitation: Invitation) -> None:
    """Try committed delivery; a failed sink leaves durable pending intent."""
    if not invitation.audit_outbox_id:
        return
    from jvspatial.core.context import get_default_context

    from app.services.entry_write_scope import is_postgres_entry_transaction

    database = get_default_context().database
    if is_postgres_entry_transaction(database):
        return  # The outer command owns commit and outbox recovery.
    from app.services.app_operations.event_outbox import deliver_operation_event

    try:
        await deliver_operation_event(
            outbox_id=invitation.audit_outbox_id, database=database
        )
    except Exception:
        logger.exception("Invitation audit delivery pending for %s", invitation.id)


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
    from app.services.email_identity import current_account_email

    return await current_account_email(user)


async def _invitee_email_matches_user(invitation: Invitation, user: User) -> bool:
    """True when the signed-in principal's email matches the invitation."""
    try:
        invite_email = await _normalize_email(invitation.email or "")
    except Exception:
        return False
    auth_email = await _auth_email_for_graph_user(user)
    if not auth_email:
        return False
    return secrets.compare_digest(
        invite_email.encode("utf-8"), auth_email.encode("utf-8")
    )


async def _verified_invitee_for_id(
    invitation: Invitation, user_id: str
) -> Tuple[Optional[User], Optional[str]]:
    """An invitation ID supplies no mailbox proof, unlike its bearer token."""
    from app.services.email_identity import verified_current_email
    from app.services.permissions import get_user_node

    user = await get_user_node(user_id)
    if not user:
        return None, ERR_USER_NOT_FOUND
    email = await verified_current_email(user)
    if not email:
        return user, ERR_EMAIL_UNVERIFIED
    if not secrets.compare_digest(
        email.encode("utf-8"), invitation.email.strip().casefold().encode("utf-8")
    ):
        return user, ERR_EMAIL_MISMATCH
    return user, None


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


_IssuanceArgs = ParamSpec("_IssuanceArgs")


def _atomic_invitation_issuance(
    function: Callable[_IssuanceArgs, Awaitable[Tuple[Invitation, str, str]]],
) -> Callable[_IssuanceArgs, Awaitable[Tuple[Invitation, str, str]]]:
    """Join source admission, new rooted state and audit before delivery.

    This serializes issuers using the workspace fence. Other ACL writers still
    need to adopt that fence before full distributed source revocation closes.
    """

    @wraps(function)
    async def issue(*args: _IssuanceArgs.args, **kwargs: _IssuanceArgs.kwargs):
        from jvspatial.core.context import get_default_context

        from app.exceptions import AuthorizationUnavailableError
        from app.services.app_operations.transaction_scope import (
            OperationTransactionUnavailable,
            graph_transaction_available,
            postgres_graph_transaction,
        )
        from app.services.authorization_scope import authorization_scope
        from app.services.entry_write_scope import (
            entry_write_scope,
            is_postgres_entry_transaction,
        )
        from app.services.resource_location import (
            MANAGEMENT_APP_STATES,
            locate_resource,
        )

        workspace = kwargs.get("workspace")
        workspace_id = (
            workspace.id
            if isinstance(workspace, Workspace)
            else kwargs.get("workspace_id")
        )
        if not workspace_id:
            location = await locate_resource(
                kwargs.get("resource_type"),
                kwargs.get("resource_id"),
                allowed_app_states=MANAGEMENT_APP_STATES,
            )
            if location is None:
                raise AuthorizationUnavailableError()
            workspace_id = location.workspace_id
        caller_context = get_default_context()
        if is_postgres_entry_transaction(caller_context.database):
            # Delivery has no outer-transaction commit callback yet. Refuse
            # before creation instead of releasing a token for uncommitted intent.
            raise OperationTransactionUnavailable(
                "Invitation issuance must own its graph transaction and delivery boundary"
            )
        database = caller_context.database
        if graph_transaction_available(database):
            async with (
                postgres_graph_transaction(database),
                authorization_scope(
                    workspace_id,
                    authority_mutation=True,
                ),
            ):
                result = await function(*args, **kwargs)
        else:
            async with entry_write_scope(f"invitation-issuance:{workspace_id}"):
                result = await function(*args, **kwargs)
        invitation, _, url = result
        await invitation.set_context(caller_context)
        await _deliver_invitation_audit(invitation)
        await _deliver_new_invitation(
            invitation,
            url,
            send_email_notification=kwargs.get("send_email_notification", True),
            inviter_display_name=kwargs.get("inviter_display_name"),
            resource_label=kwargs.get("resource_label", ""),
        )
        return result

    return issue


async def _deliver_new_invitation(
    invitation: Invitation,
    acceptance_url: str,
    *,
    send_email_notification: bool,
    inviter_display_name: Optional[str],
    resource_label: str,
) -> None:
    """Delivery occurs after graph commit; it never grants access."""
    workspace = await Workspace.find_one({"id": invitation.workspace_id})
    label = resource_label or (workspace.name if workspace else "a workspace")
    role = invitation.target_resource_role or invitation.role
    if send_email_notification:
        with contextlib.suppress(Exception):
            msg = render_invitation_email(
                recipient_email=invitation.email,
                inviter_name=inviter_display_name,
                organization_name=label,
                role=role,
                acceptance_url=acceptance_url,
                message=invitation.message,
                expires_days=settings.INVITATION_EXPIRY_DAYS,
            )
            await send_email(msg)
    if invitation.invited_user_id:
        await _emit_invitation_in_app_notification(
            invitation=invitation,
            invited_user_id=invitation.invited_user_id,
            inviter_user_id=invitation.invited_by_user_id,
            inviter_display_name=inviter_display_name,
            target_label=label,
            role=role,
        )


@_atomic_invitation_issuance
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

    The service validates the current issuer, target and delegation ceiling;
    callers cannot bypass these checks by invoking it without the HTTP layer.

    Raises ValueError with one of the ERR_* codes for create-time failures.
    """
    from app.api.validators_common import validate_email
    from app.services.invitation_authority import require_workspace_invitation_authority

    workspace, inviter_user_id, issuer_membership_id = (
        await require_workspace_invitation_authority(
            inviter_user_id,
            workspace.id,
            role=role,
            can_create_apps=can_create_apps,
            can_create_tracks=can_create_tracks,
        )
    )

    try:
        normalized_email = validate_email(email)
    except Exception:
        raise ValueError("invitation.email_invalid")

    invited_user_id = await _resolve_user_id_for_email(normalized_email)

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

    from app.services.invitation_grants import capture_grant_baseline

    baseline = await capture_grant_baseline(
        invited_user_id, workspace.id, workspace_membership=True
    )
    plaintext, token_hash = _generate_token()
    now_iso = _now_utc().isoformat()
    invitation = await Invitation.create(
        workspace_id=workspace.id,
        email=normalized_email,
        invited_user_id=invited_user_id,
        invited_by_user_id=inviter_user_id,
        issuer_membership_id=issuer_membership_id,
        grant_baseline=baseline,
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
    await catalog_invitation(invitation)

    acceptance_url = _build_acceptance_url(plaintext)

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
        ws = await Workspace.find_one({"id": invitation.workspace_id})
        if ws is None and not is_resource_invite:
            return ERR_WORKSPACE_NOT_FOUND
        if ws is not None and not is_resource_invite:
            from app.services.invitation_grants import apply_invitation_grant

            invite_role = invitation.role or "member"
            result = await apply_invitation_grant(
                user,
                ws,
                baseline=invitation.grant_baseline,
                workspace_membership=True,
                role=invite_role,
                joined_at=_now_utc().isoformat(),
                can_create_apps=bool(invitation.can_create_apps),
                can_create_tracks=bool(invitation.can_create_tracks),
            )
            invitation.grant_outcome = result["outcome"]
            invitation.granted_role = result["role"]
            invitation.granted_can_create_apps = result["can_create_apps"]
            invitation.granted_can_create_tracks = result["can_create_tracks"]
    elif not is_resource_invite:
        return ERR_WORKSPACE_NOT_FOUND

    if not is_resource_invite:
        return None

    from app.services.sharing import _load_resource, ensure_guest_membership

    resource = await _load_resource(
        invitation.target_resource_type,  # type: ignore[arg-type]
        invitation.target_resource_id,
    )
    if resource is None:
        return ERR_RESOURCE_NOT_FOUND

    from app.services.invitation_grants import apply_invitation_grant

    result = await apply_invitation_grant(
        user,
        resource,
        baseline=invitation.grant_baseline,
        workspace_membership=False,
        role=invitation.target_resource_role,
        invited_at=_now_utc().isoformat(),
        invited_by=invitation.invited_by_user_id,
    )
    invitation.grant_outcome = result["outcome"]
    invitation.granted_role = result["role"]
    if invitation.workspace_id and result["role"] is not None:
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
    return None


async def _finalize_invitation_acceptance(
    *,
    invitation: Invitation,
    user: User,
    accepting_user_id: str,
    require_verified_email: bool = False,
    expected_token_hash: Optional[str] = None,
) -> Tuple[Optional[Invitation], Optional[str]]:
    """Join current invitation admission, grant and terminal state on PostgreSQL."""
    from app.services.invitation_lifecycle import invitation_lifecycle_scope
    from app.services.permissions import get_user_node

    try:
        async with invitation_lifecycle_scope(
            invitation.id, invitation.workspace_id
        ) as current:
            if expected_token_hash is not None and not secrets.compare_digest(
                current.token_hash, expected_token_hash
            ):
                return current, ERR_INVALID_TOKEN
            user = await get_user_node(accepting_user_id)
            if user is None:
                return current, ERR_USER_NOT_FOUND
            if require_verified_email:
                _, proof_error = await _verified_invitee_for_id(current, user.id)
                if proof_error:
                    return current, proof_error
            elif not await _invitee_email_matches_user(current, user):
                return current, ERR_EMAIL_MISMATCH
            if current.status == "accepted":
                return current, (
                    None if current.invited_user_id == user.id else ERR_CONSUMED
                )
            if current.status != "pending":
                return current, {
                    "revoked": ERR_REVOKED,
                    "declined": ERR_DECLINED,
                    "expired": ERR_EXPIRED,
                }.get(current.status, ERR_INVALID_TOKEN)
            if _is_past(current.expires_at):
                return current, ERR_EXPIRED
            result = await _accept_admitted_invitation(
                invitation=current, user=user, accepting_user_id=accepting_user_id
            )
        await _deliver_invitation_audit(current)
        return result
    except _InvitationGrantFailure as exc:
        return invitation, exc.code
    except Exception:
        logger.exception(
            "failed to materialize grants for invitation %s", invitation.id
        )
        return invitation, ERR_GRANT_FAILED


class _InvitationGrantFailure(RuntimeError):
    """A soft materializer failure must unwind the graph unit before returning."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


async def _accept_admitted_invitation(
    *, invitation: Invitation, user: User, accepting_user_id: str
) -> Tuple[Optional[Invitation], Optional[str]]:
    """Apply grant and terminal state inside the caller's held lifecycle scope."""
    from app.services.invitation_authority import require_current_invitation_issuer

    prior = _invitation_snapshot(invitation)
    await require_current_invitation_issuer(invitation)
    grant_err = await _materialize_invitation_grants(user, invitation)
    if grant_err:
        raise _InvitationGrantFailure(grant_err)

    # Acceptance may grant membership and a direct collaborator edge after a
    # prior access check cached denial under the auth principal. Make the
    # accepted resource readable immediately, including in this process.
    from app.services.permissions_process_cache import invalidate_user_aliases

    invalidate_user_aliases(user)

    if invitation.target_resource_type:
        from app.services.permissions import resolve_role

        invitation.effective_role_at_acceptance = await resolve_role(
            user.id, invitation.target_resource_type, invitation.target_resource_id
        )
    else:
        from app.services.workspace_permissions import can_access_workspace

        current_role = await can_access_workspace(user.id, invitation.workspace_id)
        invitation.effective_role_at_acceptance = (
            None if current_role == "none" else current_role
        )

    invitation.status = "accepted"
    invitation.invitation_revision += 1
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
    from app.services.permissions import get_user_node

    user = await get_user_node(accepting_user_id)
    if not user:
        return invitation, ERR_USER_NOT_FOUND

    if not await _invitee_email_matches_user(invitation, user):
        return invitation, ERR_EMAIL_MISMATCH

    return await _finalize_invitation_acceptance(
        invitation=invitation,
        user=user,
        accepting_user_id=accepting_user_id,
        expected_token_hash=hashlib.sha256(plaintext.encode("utf-8")).hexdigest(),
    )


async def accept_invitation_by_id(
    invitation_id: str, accepting_user_id: str
) -> Tuple[Optional[Invitation], Optional[str]]:
    """Accept a pending invitation by id for the authenticated invitee."""
    invitation = await Invitation.get(invitation_id)
    if not invitation:
        return None, ERR_NOT_FOUND
    user, proof_error = await _verified_invitee_for_id(invitation, accepting_user_id)
    if proof_error or user is None:
        return invitation, proof_error
    preview_err: Optional[str] = None
    if invitation.status == "revoked":
        preview_err = ERR_REVOKED
    elif invitation.status == "declined":
        preview_err = ERR_DECLINED
    elif invitation.status == "expired" or _is_past(invitation.expires_at):
        preview_err = ERR_EXPIRED
    elif invitation.status not in {"pending", "accepted"}:
        preview_err = ERR_INVALID_TOKEN
    if preview_err:
        return invitation, preview_err

    return await _finalize_invitation_acceptance(
        invitation=invitation,
        user=user,
        accepting_user_id=accepting_user_id,
        require_verified_email=True,
    )


@_atomic_invitation_issuance
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
    from app.services.sharing import VALID_ROLES

    if role not in VALID_ROLES:
        raise ValueError("invitation.invalid_role")

    from app.services.invitation_authority import require_resource_invitation_authority

    workspace_id, inviter_user_id, issuer_membership_id = (
        await require_resource_invitation_authority(
            inviter_user_id,
            resource_type,
            resource_id,
            workspace_id,
            role=role,
        )
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

    from app.services.invitation_grants import capture_grant_baseline

    baseline = await capture_grant_baseline(
        invited_user_id, resource_id, workspace_membership=False
    )
    plaintext, token_hash = _generate_token()
    now_iso = _now_utc().isoformat()
    invitation = await Invitation.create(
        workspace_id=workspace_id or "",
        email=normalized_email,
        invited_user_id=invited_user_id,
        invited_by_user_id=inviter_user_id,
        issuer_membership_id=issuer_membership_id,
        grant_baseline=baseline,
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
    await catalog_invitation(invitation)

    acceptance_url = _build_acceptance_url(plaintext)

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
    require_verified_email: bool = False,
    expected_token_hash: Optional[str] = None,
) -> Tuple[Invitation, Optional[str]]:
    """Recheck proof and pending state while holding the lifecycle unit."""
    from app.services.invitation_lifecycle import invitation_lifecycle_scope
    from app.services.permissions import get_user_node

    async with invitation_lifecycle_scope(
        invitation.id, invitation.workspace_id
    ) as current:
        if expected_token_hash is not None and not secrets.compare_digest(
            current.token_hash, expected_token_hash
        ):
            return current, ERR_INVALID_TOKEN
        current_user = await get_user_node(declining_user_id)
        if current_user is None:
            return current, ERR_USER_NOT_FOUND
        if require_verified_email:
            _, proof_error = await _verified_invitee_for_id(current, current_user.id)
            if proof_error:
                return current, proof_error
        elif not await _invitee_email_matches_user(current, current_user):
            return current, ERR_EMAIL_MISMATCH
        if current.status != "pending":
            return current, {
                "accepted": ERR_CONSUMED,
                "declined": ERR_DECLINED,
                "revoked": ERR_REVOKED,
                "expired": ERR_EXPIRED,
            }.get(current.status, ERR_INVALID_TOKEN)
        if _is_past(current.expires_at):
            return current, ERR_EXPIRED
        result = await _decline_admitted_invitation(
            invitation=current,
            user=current_user,
            declining_user_id=declining_user_id,
        )
    await _deliver_invitation_audit(current)
    return result


async def _decline_admitted_invitation(
    *, invitation: Invitation, user: User, declining_user_id: str
) -> Tuple[Invitation, None]:
    """Persist the terminal decline after current state and recipient proof."""
    prior = _invitation_snapshot(invitation)
    invitation.status = "declined"
    invitation.invitation_revision += 1
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
        expected_token_hash=hashlib.sha256(plaintext.encode("utf-8")).hexdigest(),
    )


async def decline_invitation_by_id(
    invitation_id: str,
    declining_user_id: str,
) -> Tuple[Optional[Invitation], Optional[str]]:
    """Decline a pending invitation by id for the authenticated invitee."""
    invitation = await Invitation.get(invitation_id)
    if not invitation:
        return None, ERR_NOT_FOUND
    user, proof_error = await _verified_invitee_for_id(invitation, declining_user_id)
    if proof_error or user is None:
        return invitation, proof_error
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

    return await _finalize_invitation_decline(
        invitation=invitation,
        user=user,
        declining_user_id=declining_user_id,
        require_verified_email=True,
    )


async def revoke_invitation(invitation: Invitation, *, actor_user_id: str) -> None:
    """Cancel one current pending invitation through the held lifecycle scope."""
    from app.api.errors import ResourceConflictError
    from app.services.invitation_authority import require_invitation_revoke_authority
    from app.services.invitation_lifecycle import invitation_lifecycle_scope

    async with invitation_lifecycle_scope(
        invitation.id, invitation.workspace_id
    ) as current:
        await require_invitation_revoke_authority(current, actor_user_id)
        if current.status == "revoked":
            invitation.status = current.status
            invitation.consumed_at = current.consumed_at
            invitation.invitation_revision = current.invitation_revision
            return
        if current.status != "pending":
            raise ResourceConflictError(message="Invitation is no longer pending")
        await _revoke_admitted_invitation(current, actor_user_id=actor_user_id)
        invitation.status = current.status
        invitation.consumed_at = current.consumed_at
        invitation.invitation_revision = current.invitation_revision
        invitation.audit_outbox_id = current.audit_outbox_id
    await _deliver_invitation_audit(current)


async def _revoke_admitted_invitation(
    invitation: Invitation, *, actor_user_id: str
) -> None:
    """Write cancellation only after current lifecycle and actor admission."""
    prior = _invitation_snapshot(invitation)
    invitation.status = "revoked"
    invitation.invitation_revision += 1
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
    from jvspatial.core.context import get_default_context

    from app.services.authority_facts import required_node_text
    from app.services.invitation_lifecycle import invitation_lifecycle_scope

    if type(batch_limit) is not int or batch_limit < 1 or batch_limit > 500:
        raise ValueError("batch_limit must be an integer between 1 and 500")
    count = 0
    now_iso = _now_utc().isoformat()
    # Node.find treats keyword arguments as field predicates, not DB options.
    # Apply the hard limit to the primary database before any hydration.
    pending = await get_default_context().database.find(
        "node",
        {
            "entity": Invitation._entity_name(),
            "context.status": "pending",
            "context.expires_at": {"$lte": now_iso},
        },
        limit=batch_limit,
    )
    for row in pending:
        invitation_id = row.get("id")
        if not isinstance(invitation_id, str) or not invitation_id:
            raise ValueError("Invalid invitation identity in expiry inventory")
        workspace_id = required_node_text(row, "workspace_id")
        async with invitation_lifecycle_scope(invitation_id, workspace_id) as current:
            if current.status != "pending" or not _is_past(current.expires_at):
                continue
            before = _invitation_snapshot(current)
            current.status = "expired"
            current.invitation_revision += 1
            current.consumed_at = now_iso
            await current.save()
            await _emit_invitation_change(
                actor_user_id="system:invitation-expiry",
                action="workspace.invitation_expire",
                system_actor=True,
                invitation=current,
                before=before,
                scope=f"ws:{workspace_id}",
            )
            count += 1
        await _deliver_invitation_audit(current)
    return count
