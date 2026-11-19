"""Personal WhatsApp channel binding: encrypted jvconnect credentials.

The binding is user-owned and scoped to that user's personal workspace.
Ciphertext is bound to the binding id (GCM AAD). Public reads go through
``public_binding_view``.
"""

from __future__ import annotations

import logging

from app.agentive.edges import HAS_WHATSAPP_CHANNEL_BINDING
from app.agentive.nodes import WhatsAppChannelBinding
from app.api.errors import ResourceConflictError, ResourceNotFoundError
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


async def _require_owner(user_id: str):
    from app.services.permissions import get_user_node
    from app.services.personal_workspace import ensure_personal_workspace

    user = await get_user_node(user_id)
    if user is None:
        raise ResourceNotFoundError(message="User not found")
    workspace = await ensure_personal_workspace(user)
    return user, workspace


async def get_whatsapp_channel_binding(
    user_id: str,
) -> WhatsAppChannelBinding | None:
    """Return the caller's binding, or None when they have not saved one."""
    found = await WhatsAppChannelBinding.find(user_id=user_id)
    return found[0] if found else None


async def create_whatsapp_channel_binding(
    *,
    user_id: str,
    jvconnect_base_url: str,
    api_key: str,
) -> WhatsAppChannelBinding:
    """Create one binding and wire it to the user in the same call."""
    if not encryption_available():
        raise RuntimeError(
            encryption_unavailable_reason() or "INTEGRAL_CREDENTIAL_ENC_KEY is required"
        )
    existing = await get_whatsapp_channel_binding(user_id)
    if existing is not None:
        raise ResourceConflictError(
            message="WhatsApp channel is already configured for this user"
        )

    user, workspace = await _require_owner(user_id)
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
        binding.api_key_ciphertext = encrypt_secret_for_storage(api_key, aad=binding.id)
        binding.api_key_last4 = api_key[-4:] if len(api_key) >= 4 else api_key
        binding.updated_at = utc_now_iso()
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


async def disable_whatsapp_channel_binding_for_owner(
    user_id: str, binding_id: str
) -> WhatsAppChannelBinding:
    """Disable one binding. Another user's id is indistinguishable from a miss."""
    binding = await WhatsAppChannelBinding.get(binding_id)
    if binding is None or binding.user_id != user_id:
        raise ResourceNotFoundError(message="WhatsApp channel is not configured")
    binding.enabled = False
    binding.updated_at = utc_now_iso()
    await binding.save()
    return binding
