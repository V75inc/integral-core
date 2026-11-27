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
_IDENTITY_SECRET_KEYS = frozenset(
    {
        "otp_hash",
        "otp_expires",
        "otp_attempts",
        "link_token",
        "link_token_expires",
    }
)


def _public_identity(data: Dict[str, Any]) -> Dict[str, Any]:
    """Drop verification secrets from a channel identity shown in settings."""
    preferences = data.get("preferences")
    if isinstance(preferences, dict):
        data["preferences"] = {
            key: value
            for key, value in preferences.items()
            if key not in _IDENTITY_SECRET_KEYS
        }
    return data


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
    items = [_public_identity(await export_node(ci)) for ci in identities]
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
    await require_platform_admin(request)
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

    Return an account-issued pairing code once; only its hash is stored.
    The user sends that exact code from the claimed WhatsApp number.
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


def _require_channel_user(request: Request) -> str:
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    return user_id


def _binding_validation_message(exc: Exception) -> str:
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return "Invalid WhatsApp channel binding"
    details = errors()
    if not details:
        return "Invalid WhatsApp channel binding"
    message = str(details[0].get("msg") or "Invalid WhatsApp channel binding")
    prefix = "Value error, "
    if message.startswith(prefix):
        message = message[len(prefix) :]
    return message


@endpoint(
    "/agentive/channels/whatsapp/binding",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def get_whatsapp_channel_binding_endpoint(request: Request) -> Dict[str, Any]:
    """Return the active workspace's WhatsApp line. Secrets are omitted."""
    from app.agentive.services.whatsapp_channel_binding import (
        attach_jvconnect_calling_status,
        binding_for_workspace,
        workspace_calling_view,
        workspace_line_grant,
    )
    from app.services.request_scope import resolve_workspace_id_from_request

    user_id = _require_channel_user(request)
    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    if workspace_id:
        from app.services.workspace_permissions import is_workspace_admin_or_owner

        if not await is_workspace_admin_or_owner(user_id, workspace_id):
            raise InsufficientPermissionsError(
                message="Only workspace owners and admins can view the WhatsApp line"
            )
    binding = None if not workspace_id else await binding_for_workspace(workspace_id)
    from app.connectors.package_channels import read_host_grant

    grant_edge = None
    view = None
    if binding is not None and workspace_id:
        grant_edge = await workspace_line_grant(workspace_id, binding.id)
        view = workspace_calling_view(binding, grant_edge)
        view = await attach_jvconnect_calling_status(binding, view)
    return {
        "binding": view,
        "grant": read_host_grant("whatsapp", binding),
    }


@endpoint(
    "/agentive/channels/whatsapp/binding",
    methods=["PUT"],
    auth=True,
    tags=["Agentive"],
)
async def save_whatsapp_channel_binding_endpoint(request: Request) -> Dict[str, Any]:
    """Attach the active workspace to the jvconnect line for this API key.

    The channel stays disabled until webhook registration succeeds.
    """
    from pydantic import ValidationError

    from app.agentive.services.whatsapp_channel_binding import (
        binding_for_workspace,
        public_binding_view,
        save_whatsapp_channel_credentials,
    )
    from app.api.errors import ServiceUnavailableError
    from app.schemas.agentive.whatsapp_channel import SaveWhatsAppChannelBindingRequest
    from app.services.request_scope import resolve_workspace_id_from_request

    user_id = _require_channel_user(request)
    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    if not workspace_id:
        raise BadRequestError(message="A workspace is required")
    try:
        raw = await request.json()
    except Exception as exc:
        raise BadRequestError(message="Request body must be a JSON object") from exc
    try:
        body = SaveWhatsAppChannelBindingRequest.model_validate(raw or {})
    except ValidationError as exc:
        raise BadRequestError(message=_binding_validation_message(exc)) from exc
    before_binding = await binding_for_workspace(workspace_id)
    before = None if before_binding is None else public_binding_view(before_binding)
    try:
        binding, created = await save_whatsapp_channel_credentials(
            user_id=user_id,
            workspace_id=workspace_id,
            jvconnect_base_url=body.jvconnect_base_url,
            api_key=body.api_key,
            request_base_url=str(request.base_url),
            expected_binding_id=body.expected_binding_id,
            check_expected_binding="expected_binding_id" in body.model_fields_set,
        )
    except RuntimeError as exc:
        text = str(exc)
        if "INTEGRAL_CREDENTIAL_ENC_KEY" not in text:
            raise
        raise ServiceUnavailableError(message=text) from exc
    view = public_binding_view(binding)
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action=(
            "whatsapp_channel_binding.create"
            if created
            else "whatsapp_channel_binding.update"
        ),
        resource_type="WhatsAppChannelBinding",
        resource_id=binding.id,
        before=before,
        after=view,
        scope=f"user:{user_id}",
    )
    return {"binding": view}


@endpoint(
    "/agentive/channels/whatsapp/binding/calling",
    methods=["PUT"],
    auth=True,
    tags=["Agentive"],
)
async def set_whatsapp_calling_endpoint(request: Request) -> Dict[str, Any]:
    """Turn WhatsApp calling on or off for the active workspace line."""
    from pydantic import ValidationError

    from app.agentive.services.whatsapp_channel_binding import (
        set_workspace_line_calling_enabled,
        workspace_calling_view,
    )
    from app.schemas.agentive.whatsapp_channel import SetWhatsAppCallingRequest
    from app.services.request_scope import resolve_workspace_id_from_request

    user_id = _require_channel_user(request)
    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    try:
        raw = await request.json()
    except Exception as exc:
        raise BadRequestError(message="Request body must be a JSON object") from exc
    try:
        body = SetWhatsAppCallingRequest.model_validate(raw or {})
    except ValidationError as exc:
        raise BadRequestError(message=_binding_validation_message(exc)) from exc
    binding, grant, status = await set_workspace_line_calling_enabled(
        user_id,
        workspace_id,
        body.expected_grant_id,
        body.expected_revision,
        body.calling_enabled,
    )
    view = workspace_calling_view(binding, grant)
    view["jvconnect_calling_status"] = status
    if view["calling_revision"] != body.expected_revision:
        await emit_change_event(
            actor_kind="human",
            actor_id=user_id,
            action="whatsapp_channel_calling.update",
            resource_type="UsesWhatsAppLine",
            resource_id=grant.id,
            before={
                "calling_enabled": not body.calling_enabled,
                "revision": body.expected_revision,
            },
            after={
                "calling_enabled": view["calling_enabled"],
                "revision": view["calling_revision"],
            },
            scope=f"user:{user_id}",
        )
    return {"binding": view}


@endpoint(
    "/agentive/channels/whatsapp/binding/calling/service",
    methods=["PUT"],
    auth=True,
    tags=["Agentive"],
)
async def set_whatsapp_voice_service_endpoint(request: Request) -> Dict[str, Any]:
    """Save the jvvoice URL and key for this workspace line."""
    from pydantic import ValidationError

    from app.agentive.services.whatsapp_channel_binding import (
        attach_jvconnect_calling_status,
        binding_for_workspace,
        set_workspace_line_voice_service,
        workspace_calling_view,
        workspace_line_grant,
    )
    from app.api.errors import ServiceUnavailableError
    from app.schemas.agentive.whatsapp_channel import SaveWhatsAppVoiceServiceRequest
    from app.services.request_scope import resolve_workspace_id_from_request

    user_id = _require_channel_user(request)
    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    try:
        raw = await request.json()
    except Exception as exc:
        raise BadRequestError(message="Request body must be a JSON object") from exc
    try:
        body = SaveWhatsAppVoiceServiceRequest.model_validate(raw or {})
    except ValidationError as exc:
        raise BadRequestError(message=_binding_validation_message(exc)) from exc
    before_binding = await binding_for_workspace(workspace_id)
    before = None
    if before_binding is not None:
        before = {
            "jvvoice_base_url": getattr(before_binding, "jvvoice_base_url", "") or "",
            "jvvoice_api_key_last4": getattr(
                before_binding, "jvvoice_api_key_last4", ""
            )
            or "",
        }
    try:
        binding = await set_workspace_line_voice_service(
            user_id, workspace_id, body.jvvoice_base_url, body.api_key
        )
    except RuntimeError as exc:
        text = str(exc)
        if "INTEGRAL_CREDENTIAL_ENC_KEY" not in text:
            raise
        raise ServiceUnavailableError(message=text) from exc
    grant = await workspace_line_grant(workspace_id, binding.id)
    view = workspace_calling_view(binding, grant)
    view = await attach_jvconnect_calling_status(binding, view)
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="whatsapp_channel_voice_service.update",
        resource_type="WhatsAppChannelBinding",
        resource_id=binding.id,
        before=before,
        after={
            "jvvoice_base_url": view.get("jvvoice_base_url") or "",
            "jvvoice_api_key_last4": view.get("jvvoice_api_key_last4") or "",
        },
        scope=f"user:{user_id}",
    )
    return {"binding": view}


@endpoint(
    "/agentive/channels/whatsapp/binding/webhook",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def reregister_whatsapp_webhook_endpoint(request: Request) -> Dict[str, Any]:
    """Register the current public webhook URL on the bound JV Connect line."""
    from app.agentive.services.whatsapp_channel_binding import (
        attach_jvconnect_calling_status,
        reregister_workspace_whatsapp_webhook,
        workspace_calling_view,
        workspace_line_grant,
    )
    from app.services.request_scope import resolve_workspace_id_from_request

    user_id = _require_channel_user(request)
    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    binding = await reregister_workspace_whatsapp_webhook(
        user_id, workspace_id, str(request.base_url)
    )
    grant = await workspace_line_grant(workspace_id, binding.id)
    view = workspace_calling_view(binding, grant)
    view = await attach_jvconnect_calling_status(binding, view)
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="whatsapp_channel_webhook.register",
        resource_type="WhatsAppChannelBinding",
        resource_id=binding.id,
        before=None,
        after={
            "webhook_registered": view.get("webhook_registered"),
            "enabled": view.get("enabled"),
        },
        scope=f"user:{user_id}",
    )
    return {"binding": view}


@endpoint(
    "/agentive/channels/whatsapp/binding/{binding_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Agentive"],
)
async def disable_whatsapp_channel_binding_endpoint(
    request: Request, binding_id: str
) -> Dict[str, Any]:
    """Disconnect the active workspace. The last grant disables the line."""
    from app.agentive.services.whatsapp_channel_binding import (
        disconnect_workspace_whatsapp_line,
        public_binding_view,
    )
    from app.services.request_scope import resolve_workspace_id_from_request

    user_id = _require_channel_user(request)
    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    if not workspace_id:
        raise BadRequestError(message="A workspace is required")
    binding = await disconnect_workspace_whatsapp_line(
        user_id, workspace_id, binding_id
    )
    view = public_binding_view(binding)
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="whatsapp_channel_binding.disable",
        resource_type="WhatsAppChannelBinding",
        resource_id=binding.id,
        before=None,
        after=view,
        scope=f"user:{user_id}",
    )
    return {"binding": view}


_MAX_WHATSAPP_WEBHOOK_BYTES = 1_000_000


def _whatsapp_signature_header(request: Request) -> str:
    primary = (request.headers.get("x-jvconnect-signature") or "").strip()
    if primary:
        return primary
    return (request.headers.get("x-hub-signature-256") or "").strip()


@endpoint(
    "/agentive/channels/whatsapp/webhook",
    methods=["POST"],
    auth=False,
    tags=["Agentive"],
)
async def whatsapp_channel_webhook(request: Request) -> Dict[str, Any]:
    """Accept a signed WhatsApp forward. No user JWT is required."""
    raw = await request.body()
    if len(raw) > _MAX_WHATSAPP_WEBHOOK_BYTES:
        raise BadRequestError(message="Webhook body is too large")
    from app.agentive.services.whatsapp_ingress import receive_whatsapp_webhook

    return await receive_whatsapp_webhook(raw, _whatsapp_signature_header(request))
