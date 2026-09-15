"""Channel identity CRUD endpoints with resolve and OTP verification.

Phase 9 Plan 09-03a (NOTIF-02, A3) extends :func:`whatsapp_verify_otp` with
the WhatsApp opt-in capture moment — after the OTP verifies and the
:class:`ChannelIdentity` row is freshly verified, this module records the
opt-in on ``User.notification_preferences.whatsapp.opted_in_at`` +
``phone_e164`` and dispatches a ``whatsapp_welcome`` notification via the
single-entry router. The real WhatsApp send fires in Plan 09-03b once the
``WhatsappChannel`` adapter replaces the ``_WhatsappStub``.

The opt-in capture is intentionally placed in this module (the agentive
verify-otp endpoint) rather than client-side or in a dedicated settings
endpoint — verifying the phone IS the consent capture event (one OTP
round-trip per user). T-09-03a-S01 mitigation: the target user is
always the freshly-verified ChannelIdentity owner (``ci.user_id``), never a
caller-supplied value.
"""

import logging
from datetime import UTC, datetime, timezone
from typing import Any, Dict, Optional

from fastapi import Request
from jvspatial.api import endpoint

from app.agentive.nodes import ChannelIdentity
from app.agentive.services.channel_identity import (
    find_conflicting_verified_identity,
    initiate_link,
    resolve_channel_identity,
    resolve_org_user,
    verify_channel_otp,
    verify_identity_by_token,
)
from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    InternalServerError,
    MissingAuthenticationError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.api.utils import export_node, require_platform_admin, resolve_principal_id
from app.models.nodes import User
from app.schemas.notification_preferences import default_preferences
from app.services import notification_router
from app.services.change_event import emit_change_event

logger = logging.getLogger(__name__)

_VALID_CHANNELS = ("whatsapp", "slack", "telegram", "email", "sms", "in_app")


@endpoint(
    "/agentive/channels/identities",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def list_channel_identities(request: Request) -> Dict[str, Any]:
    """List channel identities linked to the authenticated user."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    identities = await ChannelIdentity.find(user_id=user_id)
    items = []
    for ci in identities:
        data = ci.export() if hasattr(ci, "export") else {"id": ci.id}
        items.append(data)
    return {"identities": items, "total": len(items)}


@endpoint(
    "/agentive/channels/identities",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def create_channel_identity(
    request: Request,
    channel: str = "",
    channel_user_id: str = "",
    preferences: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create and link a channel identity for the authenticated user."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    if channel not in _VALID_CHANNELS:
        raise BadRequestError(
            message="channel must be whatsapp, slack, telegram, email, sms, or in_app"
        )

    now = datetime.now(timezone.utc).isoformat()
    ci = await ChannelIdentity.create(
        user_id=user_id,
        channel=channel,
        channel_user_id=channel_user_id,
        preferences=dict(preferences or {}),
        created_at=now,
    )
    # Phase 10.5 Plan 10.5-08 (I-GRAPH-01): wire User -HAS_CHANNEL_IDENTITY-> ChannelIdentity
    # using the canonical agentive-layer edge (already wired in
    # services/channel_identity.py for the OTP-init path; channels.py
    # endpoint was the unwired sibling site).
    # Fail closed with rollback — never leave an orphan ChannelIdentity.
    try:
        from app.agentive.edges import HAS_CHANNEL_IDENTITY
        from app.services.permissions import get_user_node

        owner = await get_user_node(user_id)
        if owner is None:
            raise RuntimeError(f"User node not found for {user_id}")
        await owner.connect(
            ci, edge=HAS_CHANNEL_IDENTITY, is_primary=False, created_at=now
        )
    except Exception as e:
        logger.warning(
            "channels.link: HAS_CHANNEL_IDENTITY wire failed: %s; "
            "rolling back orphaned ChannelIdentity %s",
            e,
            ci.id,
        )
        try:
            await ci.delete()
        except Exception:
            logger.exception(
                "channels.link: rollback delete failed for ChannelIdentity=%s",
                ci.id,
            )
        raise InternalServerError(message="Failed to link channel identity")

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="channel_identity.create",
        resource_type="ChannelIdentity",
        resource_id=ci.id,
        before=None,
        after=await export_node(ci),
        scope=f"user:{user_id}",
    )

    return {
        "identity": await export_node(ci),
        "message": "Channel identity linked",
    }


@endpoint(
    "/agentive/channels/identities/{identity_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Agentive"],
)
async def delete_channel_identity(request: Request, identity_id: str) -> Dict[str, Any]:
    """Delete a linked channel identity by ID. Owner only (404 otherwise)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    ci = await ChannelIdentity.get(identity_id)
    # 404 (not 403) on cross-user access — no existence leak.
    if not ci or ci.user_id != user_id:
        raise ResourceNotFoundError(message="Channel identity not found")
    prior_snapshot = await export_node(ci)  # D-03 before-snapshot
    await ci.delete()

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="channel_identity.delete",
        resource_type="ChannelIdentity",
        resource_id=identity_id,
        before=prior_snapshot,
        after=None,
        scope=f"user:{user_id}",
    )

    return {"message": "Channel identity unlinked", "identity_id": identity_id}


@endpoint(
    "/agentive/channels/identities/{identity_id}/verify",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def verify_channel_identity(request: Request, identity_id: str) -> Dict[str, Any]:
    """Mark a channel identity as verified WITHOUT an OTP / link token.

    Platform-admin only. Any authenticated user could previously bind and
    self-verify an arbitrary phone number here, hijacking inbound-channel
    resolution for that address. End users verify through the OTP
    (``/whatsapp/verify-otp``) or link-token (``/verify-link-token``) paths.
    """
    require_platform_admin(request)
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    ci = await ChannelIdentity.get(identity_id)
    if not ci:
        raise ResourceNotFoundError(message="Channel identity not found")

    if await find_conflicting_verified_identity(
        ci.channel, ci.channel_user_id, exclude_id=ci.id
    ):
        raise ResourceConflictError(
            message="This channel address is already verified for another account"
        )

    prior_snapshot = await export_node(ci)  # D-03 before-snapshot
    ci.verified = True
    ci.verified_at = datetime.now(timezone.utc).isoformat()
    await ci.save()

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="channel_identity.verify",
        resource_type="ChannelIdentity",
        resource_id=ci.id,
        before=prior_snapshot,
        after=await export_node(ci),
        scope=f"user:{user_id}",
    )

    return {"message": "Channel identity verified", "identity_id": identity_id}


@endpoint(
    "/agentive/channels/identities/resolve",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def resolve_identity(
    request: Request,
    channel: str = "",
    channel_user_id: str = "",
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve a channel identity to an Integral user.

    Used by agents to map WhatsApp phone number → Integral user.
    Requires service auth (X-Integral-Service-Key + signature) — a plain user
    JWT must not be able to map arbitrary phone numbers to accounts.
    """
    if not getattr(request.state, "service_auth", False):
        raise InsufficientPermissionsError(
            message="Service authentication required for identity resolution"
        )
    if not channel or not channel_user_id:
        raise BadRequestError(message="channel and channel_user_id are required")

    result = await resolve_channel_identity(channel, channel_user_id)

    if not result and workspace_id:
        result = await resolve_org_user(channel, channel_user_id, workspace_id)

    if not result:
        return {
            "resolved": False,
            "message": f"No Integral user found for {channel}:{channel_user_id}",
        }

    return {"resolved": True, **result}


@endpoint(
    "/agentive/channels/whatsapp/verify-initiate",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def whatsapp_verify_initiate(
    request: Request,
    phone: str = "",
) -> Dict[str, Any]:
    """Initiate WhatsApp channel identity verification.

    Creates a ChannelIdentity with OTP + link token for the user to verify
    via WhatsApp. The OTP and link token are returned once for delivery.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    phone = (phone or "").strip()
    if not phone:
        raise BadRequestError(message="phone is required")

    return await initiate_link(user_id, "whatsapp", phone)


@endpoint(
    "/agentive/channels/whatsapp/verify-otp",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def whatsapp_verify_otp(
    request: Request,
    phone: str = "",
    otp_code: str = "",
) -> Dict[str, Any]:
    """Verify a WhatsApp channel identity using an OTP code.

    Called by the agent when a user sends their verification code on WhatsApp.
    Requires service auth (X-Integral-Service-Key).
    """
    phone = (phone or "").strip()
    otp_code = (otp_code or "").strip()

    if not phone or not otp_code:
        raise BadRequestError(message="phone and otp_code are required")

    result = await verify_channel_otp("whatsapp", phone, otp_code)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    # Service-key auth flow — actor_kind="connector" for WhatsApp BYOA verification.
    identity_id = (
        (result or {}).get("identity_id") if isinstance(result, dict) else None
    )
    ci = None
    if identity_id:
        ci = await ChannelIdentity.get(identity_id)
        await emit_change_event(
            actor_kind="connector",
            actor_id="whatsapp-verify",
            action="channel_identity.verify",
            resource_type="ChannelIdentity",
            resource_id=identity_id,
            before=None,  # OTP-verify is one-shot — caller already saw initiate state
            after=await export_node(ci) if ci else None,
            scope=f"user:{(ci.user_id if ci else '') or 'system'}",
        )

    # Phase 9 Plan 09-03a (NOTIF-02 / A3) — capture the WhatsApp opt-in moment
    # and dispatch the welcome message. T-09-03a-S01 mitigation: the target
    # user is ALWAYS the freshly-verified ChannelIdentity owner (ci.user_id),
    # never a caller-supplied value. The opt-in fires only after verify_otp
    # succeeds, identity_id resolves, AND ci.user_id is non-empty.
    if identity_id and ci is not None and ci.user_id:
        target = await User.get(ci.user_id)
        if target is not None:
            # Merge into existing prefs (initialize from defaults on first
            # opt-in). The dict is stored on the Node directly; the Pydantic
            # boundary in NotificationPreferences re-validates on the next
            # router resolve.
            prefs: Dict[str, Any] = (
                dict(target.notification_preferences)
                if target.notification_preferences
                else default_preferences().model_dump()
            )
            wa = dict(prefs.get("whatsapp") or {})
            wa["opted_in_at"] = datetime.now(UTC).isoformat()
            wa["phone_e164"] = phone
            prefs["whatsapp"] = wa
            target.notification_preferences = prefs
            await target.save()

            # NOT PRODUCTIZED — org-facing facet incomplete (Full Sweep R6).
            # Dispatch records skipped WhatsApp adapter until a hard-narrowed
            # channel path ships; do not treat this as a live org-facing coworker.
            # The real WhatsApp send fires in Plan 09-03b once the
            # WhatsappChannel adapter replaces the stub; in this plan the
            # router records dispatched_to[{"channel":"whatsapp","status":
            # "skipped","reason":"adapter_landing_in_09-03b"}] which 09-03b
            # retries.
            await notification_router.dispatch(
                user_id=ci.user_id,
                kind="whatsapp_welcome",
                payload={"actor_name": target.display_name or "there"},
                actor_id="system",
                actor_kind="system",
                channels=["whatsapp"],
            )

    return result


@endpoint(
    "/agentive/channels/identities/{identity_id}/verify-link-token",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def verify_link_token(
    request: Request,
    identity_id: str,
    token: str = "",
) -> Dict[str, Any]:
    """Verify a channel identity using a link token.

    Called by the agent when a user sends their link token (received from the
    Integral web UI) on the messaging channel.
    """
    token = (token or "").strip()

    if not token:
        raise BadRequestError(message="token is required")

    result = await verify_identity_by_token(identity_id, token)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    # Service-key auth flow — actor_kind="connector" for verification handshake.
    ci = await ChannelIdentity.get(identity_id)
    await emit_change_event(
        actor_kind="connector",
        actor_id="link-token-verify",
        action="channel_identity.verify",
        resource_type="ChannelIdentity",
        resource_id=identity_id,
        before=None,
        after=await export_node(ci) if ci else None,
        scope=f"user:{(ci.user_id if ci else '') or 'system'}",
    )

    return result
