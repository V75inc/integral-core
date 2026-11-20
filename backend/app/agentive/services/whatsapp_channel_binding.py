"""WhatsApp channel binding: one jvconnect line, many workspace grants.

The binding is rooted on the user who first saved the key. Each workspace
that may use the line has a ``UsesWhatsAppLine`` edge. Ciphertext is bound
to the binding id (GCM AAD). Public reads go through ``public_binding_view``.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.agentive.edges import HAS_WHATSAPP_CHANNEL_BINDING, USES_WHATSAPP_LINE
from app.agentive.nodes import WhatsAppChannelBinding
from app.api.errors import InsufficientPermissionsError, ResourceNotFoundError
from app.services.credential_crypto import (
    decrypt_secret_from_storage,
    encrypt_secret_for_storage,
    encryption_available,
    encryption_unavailable_reason,
)
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


def public_binding_view(binding: WhatsAppChannelBinding) -> dict:
    """Status safe to return to the browser. Secrets stay off this dict."""
    return {
        "id": binding.id,
        "user_id": binding.user_id,
        "workspace_id": binding.workspace_id,
        "jvconnect_base_url": binding.jvconnect_base_url,
        "phone_number_id": binding.phone_number_id,
        "display_phone": binding.display_phone,
        "enabled": binding.enabled,
        "last_error": binding.last_error,
        "api_key_last4": binding.api_key_last4,
        "webhook_registered": bool(binding.webhook_secret_ciphertext),
    }


def read_api_key(binding: WhatsAppChannelBinding) -> str:
    """Decrypt the stored jvconnect API key for this binding."""
    return decrypt_secret_from_storage(binding.api_key_ciphertext, aad=binding.id)


def read_webhook_secret(binding: WhatsAppChannelBinding) -> str:
    """Decrypt the stored jvconnect webhook secret for this binding."""
    return decrypt_secret_from_storage(
        binding.webhook_secret_ciphertext, aad=binding.id
    )


async def _require_user(user_id: str):
    from app.services.permissions import get_user_node

    user = await get_user_node(user_id)
    if user is None:
        raise ResourceNotFoundError(message="User not found")
    return user


async def _workspace_node(workspace_id: str):
    from app.models.nodes import Workspace

    workspace = await Workspace.get(workspace_id)
    if workspace is None:
        raise ResourceNotFoundError(message="Workspace not found")
    return workspace


async def get_whatsapp_channel_binding(
    user_id: str,
) -> WhatsAppChannelBinding | None:
    """Return a binding this user registered, or None."""
    found = await WhatsAppChannelBinding.find(user_id=user_id)
    return found[0] if found else None


async def granted_workspace_ids(binding: WhatsAppChannelBinding) -> list[str]:
    """Workspaces that currently hold a grant on this line."""
    workspaces = await binding.nodes(
        edge=[USES_WHATSAPP_LINE],
        direction="in",
        node=["Workspace"],
        limit=50,
    )
    return [workspace.id for workspace in workspaces]


async def binding_for_workspace(workspace_id: str) -> WhatsAppChannelBinding | None:
    """Return the line granted to this workspace, if any."""
    from app.models.nodes import Workspace

    workspace = await Workspace.get(workspace_id)
    if workspace is None:
        return None
    lines = await workspace.nodes(
        edge=[USES_WHATSAPP_LINE],
        direction="out",
        node=["WhatsAppChannelBinding"],
        limit=5,
    )
    return lines[0] if lines else None


async def _revoke_grant(workspace, binding: WhatsAppChannelBinding) -> None:
    context = await workspace.get_context()
    edges = await context.find_edges_between(
        workspace.id, binding.id, edge_class=USES_WHATSAPP_LINE
    )
    for edge in edges:
        await edge.delete()


async def grant_whatsapp_line(
    workspace_id: str, binding: WhatsAppChannelBinding, user_id: str
) -> None:
    """Attach this workspace to the line. A workspace keeps one line."""
    workspace = await _workspace_node(workspace_id)
    current = await workspace.nodes(
        edge=[USES_WHATSAPP_LINE],
        direction="out",
        node=["WhatsAppChannelBinding"],
        limit=5,
    )
    for other in current:
        if other.id == binding.id:
            continue
        await _revoke_grant(workspace, other)
        if not await granted_workspace_ids(other):
            other.enabled = False
            other.updated_at = utc_now_iso()
            await other.save()
    linked = await workspace.nodes(
        edge=[USES_WHATSAPP_LINE],
        direction="out",
        node=["WhatsAppChannelBinding"],
        limit=5,
    )
    if any(node.id == binding.id for node in linked):
        return
    await workspace.connect(
        binding,
        edge=USES_WHATSAPP_LINE,
        attached_by=user_id,
        attached_at=utc_now_iso(),
    )


def _store_api_key(
    binding: WhatsAppChannelBinding, base_url: str, api_key: str
) -> None:
    binding.jvconnect_base_url = base_url.rstrip("/")
    binding.api_key_ciphertext = encrypt_secret_for_storage(api_key, aad=binding.id)
    binding.api_key_last4 = api_key[-4:] if len(api_key) >= 4 else api_key
    binding.updated_at = utc_now_iso()


async def _require_line_admin(user_id: str, workspace_id: str) -> None:
    from app.services.workspace_permissions import is_workspace_admin_or_owner

    if not await is_workspace_admin_or_owner(user_id, workspace_id):
        raise InsufficientPermissionsError(
            message="Only workspace owners and admins can manage WhatsApp"
        )


async def create_whatsapp_channel_binding(
    *,
    user_id: str,
    jvconnect_base_url: str,
    api_key: str,
    workspace_id: str = "",
) -> WhatsAppChannelBinding:
    """Create one binding, root it on the user, and grant one workspace."""
    if not encryption_available():
        raise RuntimeError(
            encryption_unavailable_reason() or "INTEGRAL_CREDENTIAL_ENC_KEY is required"
        )
    user = await _require_user(user_id)
    if workspace_id:
        workspace = await _workspace_node(workspace_id)
    else:
        from app.services.personal_workspace import ensure_personal_workspace

        workspace = await ensure_personal_workspace(user)
    now = utc_now_iso()
    binding = await WhatsAppChannelBinding.create(
        user_id=user_id,
        workspace_id=workspace.id,
        jvconnect_base_url=jvconnect_base_url.rstrip("/"),
        api_key_ciphertext="",
        api_key_last4="",
        webhook_secret_ciphertext="",
        phone_number_id="",
        display_phone="",
        enabled=False,
        last_error="",
        created_at=now,
        updated_at=now,
    )
    try:
        await user.connect(
            binding,
            edge=HAS_WHATSAPP_CHANNEL_BINDING,
            created_at=now,
        )
        await grant_whatsapp_line(workspace.id, binding, user_id)
        _store_api_key(binding, jvconnect_base_url, api_key)
        await binding.save()
    except Exception:
        logger.warning(
            "whatsapp binding create failed for user %s; rolling back %s",
            user_id,
            binding.id,
        )
        try:
            await binding.delete()
        except Exception:
            logger.exception(
                "whatsapp binding rollback delete failed for %s", binding.id
            )
        raise
    return binding


async def _binding_for_phone(phone: str) -> WhatsAppChannelBinding | None:
    found = await WhatsAppChannelBinding.find(phone_number_id=phone)
    for binding in found:
        if binding.phone_number_id == phone:
            return binding
    return None


async def save_whatsapp_channel_credentials(
    *,
    user_id: str,
    workspace_id: str,
    jvconnect_base_url: str,
    api_key: str,
    request_base_url: str,
    client: Optional[Any] = None,
) -> tuple[WhatsAppChannelBinding, bool]:
    """Attach the active workspace to the jvconnect line this key controls.

    The same phone id reuses the existing registration. A different phone
    creates a line. Returns the binding and whether this call created it.
    """
    await _require_line_admin(user_id, workspace_id)
    if not encryption_available():
        raise RuntimeError(
            encryption_unavailable_reason() or "INTEGRAL_CREDENTIAL_ENC_KEY is required"
        )
    from app.agentive.clients.jvconnect_whatsapp import fetch_account

    account = await fetch_account(
        jvconnect_base_url.rstrip("/"), api_key, client=client
    )
    phone = ""
    display = ""
    if account.ok:
        raw_phone = account.data.get("phone_number_id")
        if isinstance(raw_phone, str) and raw_phone.strip():
            phone = raw_phone.strip()
        raw_display = account.data.get("display_phone_number")
        if isinstance(raw_display, str):
            display = raw_display.strip()
    if phone:
        existing = await _binding_for_phone(phone)
        if existing is not None:
            _store_api_key(existing, jvconnect_base_url, api_key)
            if display:
                existing.display_phone = display
            existing.last_error = ""
            await existing.save()
            await grant_whatsapp_line(workspace_id, existing, user_id)
            if existing.enabled and existing.webhook_secret_ciphertext:
                return existing, False
            registered = await register_saved_whatsapp_channel(
                existing, request_base_url=request_base_url, client=client
            )
            return registered, False
    current = await binding_for_workspace(workspace_id)
    created = False
    if (
        phone
        and current is not None
        and current.phone_number_id
        and current.phone_number_id != phone
    ):
        current = None
    if current is None:
        binding = await create_whatsapp_channel_binding(
            user_id=user_id,
            workspace_id=workspace_id,
            jvconnect_base_url=jvconnect_base_url,
            api_key=api_key,
        )
        created = True
    else:
        _store_api_key(current, jvconnect_base_url, api_key)
        current.enabled = False
        current.last_error = ""
        await current.save()
        binding = current
    registered = await register_saved_whatsapp_channel(
        binding, request_base_url=request_base_url, client=client
    )
    return registered, created


def _public_callback_url(request_base_url: str) -> str:
    from app.config import settings

    configured = (settings.PUBLIC_API_BASE_URL or "").strip().rstrip("/")
    if configured:
        return f"{configured}/api/agentive/channels/whatsapp/webhook"
    if settings.DEBUG:
        base = (request_base_url or "").strip().rstrip("/")
        if base:
            return f"{base}/api/agentive/channels/whatsapp/webhook"
    return ""


def _safe_provider_error(message: str, api_key: str) -> str:
    text = (message or "").strip() or "WhatsApp provider request failed"
    if api_key and api_key in text:
        return "WhatsApp provider request failed"
    return text[:300]


async def _disable_with_error(
    binding: WhatsAppChannelBinding, message: str
) -> WhatsAppChannelBinding:
    binding.enabled = False
    binding.last_error = message
    binding.updated_at = utc_now_iso()
    await binding.save()
    return binding


async def register_saved_whatsapp_channel(
    binding: WhatsAppChannelBinding,
    *,
    request_base_url: str,
    client: Optional[Any] = None,
) -> WhatsAppChannelBinding:
    """Register the jvconnect webhook. Failures leave the binding disabled."""
    from app.agentive.clients.jvconnect_whatsapp import fetch_account, register_webhook

    api_key = read_api_key(binding)
    if not api_key:
        return await _disable_with_error(binding, "Could not read the stored API key")

    account = await fetch_account(binding.jvconnect_base_url, api_key, client=client)
    if not account.ok:
        return await _disable_with_error(
            binding,
            _safe_provider_error(
                account.error or "Could not read the WhatsApp account",
                api_key,
            ),
        )

    phone = account.data.get("phone_number_id")
    if not isinstance(phone, str) or not phone.strip():
        return await _disable_with_error(
            binding, "WhatsApp account did not include a phone number id"
        )
    phone = phone.strip()
    display = account.data.get("display_phone_number")
    display_phone = display.strip() if isinstance(display, str) else ""

    callback = _public_callback_url(request_base_url)
    if not callback:
        return await _disable_with_error(binding, "PUBLIC_API_BASE_URL is required")

    registered = await register_webhook(
        binding.jvconnect_base_url,
        api_key,
        callback,
        client=client,
    )
    if not registered.ok:
        return await _disable_with_error(
            binding,
            _safe_provider_error(
                registered.error or "Webhook registration failed",
                api_key,
            ),
        )
    secret = registered.data.get("webhook_secret")
    if not isinstance(secret, str) or not secret:
        return await _disable_with_error(
            binding, "Webhook registration did not return a secret"
        )

    binding.phone_number_id = phone
    binding.display_phone = display_phone
    binding.enabled = True
    binding.last_error = ""
    return await store_webhook_secret(binding, secret)


async def store_webhook_secret(
    binding: WhatsAppChannelBinding, secret: str
) -> WhatsAppChannelBinding:
    """Encrypt and persist the webhook secret returned by jvconnect."""
    binding.webhook_secret_ciphertext = encrypt_secret_for_storage(
        secret, aad=binding.id
    )
    binding.updated_at = utc_now_iso()
    await binding.save()
    return binding


async def disable_whatsapp_channel_binding(user_id: str) -> WhatsAppChannelBinding:
    """Turn the caller's channel off without deleting stored secrets."""
    binding = await get_whatsapp_channel_binding(user_id)
    if binding is None:
        raise ResourceNotFoundError(message="WhatsApp channel is not configured")
    binding.enabled = False
    binding.updated_at = utc_now_iso()
    await binding.save()
    return binding


async def disconnect_workspace_whatsapp_line(
    user_id: str, workspace_id: str, binding_id: str
) -> WhatsAppChannelBinding:
    """Remove this workspace's grant. The last grant disables the line."""
    await _require_line_admin(user_id, workspace_id)
    binding = await WhatsAppChannelBinding.get(binding_id)
    granted = [] if binding is None else await granted_workspace_ids(binding)
    if binding is None or workspace_id not in granted:
        raise ResourceNotFoundError(message="WhatsApp channel is not configured")
    workspace = await _workspace_node(workspace_id)
    await _revoke_grant(workspace, binding)
    if not await granted_workspace_ids(binding):
        binding.enabled = False
        binding.updated_at = utc_now_iso()
        await binding.save()
    else:
        binding = await WhatsAppChannelBinding.get(binding_id) or binding
    return binding
