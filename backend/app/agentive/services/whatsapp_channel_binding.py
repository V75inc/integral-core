"""WhatsApp channel binding: one jvconnect line, many workspace grants.

The binding is rooted on the user who first saved the key. Each workspace
that may use the line has a ``UsesWhatsAppLine`` edge. Ciphertext is bound
to the binding id (GCM AAD). Public reads go through ``public_binding_view``.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Optional
from weakref import WeakValueDictionary

from jvspatial.core.context import get_default_context

from app.agentive.edges import HAS_WHATSAPP_CHANNEL_BINDING, USES_WHATSAPP_LINE
from app.agentive.nodes import WhatsAppChannelBinding
from app.api.errors import (
    InsufficientPermissionsError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.services.app_operations.transaction_scope import (
    graph_transaction_available,
    postgres_graph_transaction,
)
from app.services.credential_crypto import (
    decrypt_secret_from_storage,
    encrypt_secret_for_storage,
    encryption_available,
    encryption_unavailable_reason,
)
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)
_local_workspace_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


@asynccontextmanager
async def _workspace_line_write(workspace_id: str):
    """Serialize the single grant across workers on PostgreSQL."""
    if graph_transaction_available():
        async with postgres_graph_transaction() as transaction:
            await transaction.advisory_lock("whatsapp-workspace-line", workspace_id)
            yield
    else:
        # The local JSON fixture store has no cross-worker transactions.
        lock = _local_workspace_locks.setdefault(workspace_id, asyncio.Lock())
        async with lock:
            yield


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


async def workspace_line_grant(workspace_id: str, binding_id: str):
    """Read the sole grant from a workspace to an exact provider line."""
    try:
        workspace = await _workspace_node(workspace_id)
    except ResourceNotFoundError:
        return None
    context = await workspace.get_context()
    grants = await context.find_edges_between(
        workspace.id, binding_id, edge_class=USES_WHATSAPP_LINE
    )
    return grants[0] if len(grants) == 1 else None


async def workspace_line_grant_id(workspace_id: str, binding_id: str) -> str:
    """Return the exact enabled grant identity or an empty string."""
    grant = await workspace_line_grant(workspace_id, binding_id)
    return grant.id if grant is not None and grant.enabled else ""


def workspace_line_epoch(binding, grant) -> str:
    """Bind a lifecycle request to both the grant and the line revision."""
    return f"{grant.revision}:{binding.updated_at or binding.id}"


async def set_workspace_line_enabled(
    user_id: str, workspace_id: str, grant_id: str, route_epoch: str, enabled: bool
) -> None:
    """Toggle only the current workspace grant."""
    await _require_line_admin(user_id, workspace_id)
    async with _workspace_line_write(workspace_id):
        binding = await binding_for_workspace(workspace_id)
        grant = (
            await workspace_line_grant(workspace_id, binding.id) if binding else None
        )
        if (
            grant is None
            or grant.id != grant_id
            or workspace_line_epoch(binding, grant) != route_epoch
        ):
            raise ResourceConflictError(
                "WhatsApp grant changed. Refresh and try again."
            )
        if enabled and not (
            binding.enabled
            and binding.webhook_secret_ciphertext
            and binding.api_key_ciphertext
        ):
            raise ResourceConflictError("WhatsApp line is not ready")
        grant.enabled = enabled
        grant.revision += 1
        await grant.save()


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
    async with _workspace_line_write(workspace_id):
        await _grant_whatsapp_line(
            await _workspace_node(workspace_id), binding, user_id
        )


async def _grant_whatsapp_line(
    workspace, binding: WhatsAppChannelBinding, user_id: str
) -> None:
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
    if any(node.id == binding.id for node in current):
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
    user = await _require_user(user_id)
    if workspace_id:
        workspace = await _workspace_node(workspace_id)
    else:
        from app.services.personal_workspace import ensure_personal_workspace

        workspace = await ensure_personal_workspace(user)
    async with _workspace_line_write(workspace.id):
        binding = await _create_whatsapp_channel_binding(
            await _require_user(user_id),
            user_id,
            await _workspace_node(workspace.id),
            jvconnect_base_url,
            api_key,
        )
    await binding.set_context(get_default_context())
    return binding


async def _create_whatsapp_channel_binding(
    user, user_id: str, workspace, jvconnect_base_url: str, api_key: str
) -> WhatsAppChannelBinding:
    if not encryption_available():
        raise RuntimeError(
            encryption_unavailable_reason() or "INTEGRAL_CREDENTIAL_ENC_KEY is required"
        )
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
        await _grant_whatsapp_line(workspace, binding, user_id)
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


def _check_workspace_line(
    current: WhatsAppChannelBinding | None,
    expected_binding_id: str | None,
    check_expected_binding: bool,
) -> None:
    if check_expected_binding and expected_binding_id != (
        current.id if current else None
    ):
        raise ResourceConflictError(
            message="The workspace WhatsApp connection changed. Reload before continuing."
        )


async def save_whatsapp_channel_credentials(
    *,
    user_id: str,
    workspace_id: str,
    jvconnect_base_url: str,
    api_key: str,
    request_base_url: str,
    client: Optional[Any] = None,
    expected_binding_id: str | None = None,
    check_expected_binding: bool = False,
    activate: bool = True,
) -> tuple[WhatsAppChannelBinding, bool]:
    """Attach the active workspace to the jvconnect line this key controls.

    A known phone id reuses its registration. Explicit Add expects no grant;
    Configure expects the current binding and preserves its verified number.
    Returns the binding and whether this call created it.
    """
    await _require_line_admin(user_id, workspace_id)
    if not encryption_available():
        raise RuntimeError(
            encryption_unavailable_reason() or "INTEGRAL_CREDENTIAL_ENC_KEY is required"
        )
    from app.agentive.clients.jvconnect_whatsapp import fetch_account

    if check_expected_binding:
        _check_workspace_line(
            await binding_for_workspace(workspace_id),
            expected_binding_id,
            check_expected_binding,
        )

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
    async with _workspace_line_write(workspace_id):
        current = await binding_for_workspace(workspace_id)
        _check_workspace_line(current, expected_binding_id, check_expected_binding)
        if check_expected_binding and current and current.phone_number_id:
            if not phone:
                raise ResourceConflictError(
                    message="Could not verify the WhatsApp number for these credentials. The connection was kept."
                )
            if current.phone_number_id != phone:
                raise ResourceConflictError(
                    message="Disconnect the existing WhatsApp line before adding a different number."
                )
        existing = await _binding_for_phone(phone) if phone else None
        created = False
        already_registered = False
        workspace = await _workspace_node(workspace_id)
        if existing is not None:
            if activate or (current and current.id == existing.id):
                _store_api_key(existing, jvconnect_base_url, api_key)
            if display:
                existing.display_phone = display
            existing.last_error = ""
            await existing.save()
            await _grant_whatsapp_line(workspace, existing, user_id)
            already_registered = bool(
                existing.enabled and existing.webhook_secret_ciphertext
            )
            binding = existing
        elif current is None or (
            phone and current.phone_number_id and current.phone_number_id != phone
        ):
            binding = await _create_whatsapp_channel_binding(
                await _require_user(user_id),
                user_id,
                workspace,
                jvconnect_base_url,
                api_key,
            )
            created = True
        else:
            _store_api_key(current, jvconnect_base_url, api_key)
            current.enabled = False
            current.last_error = ""
            await current.save()
            binding = current
        if not activate:
            if not binding.phone_number_id and phone:
                binding.phone_number_id = phone
                await binding.save()
            grant = await workspace_line_grant(workspace_id, binding.id)
            if grant is None:
                raise ResourceConflictError("WhatsApp grant was not saved")
            grant.enabled = False
            grant.revision += 1
            await grant.save()
    await binding.set_context(get_default_context())
    if not activate or already_registered:
        return binding, created
    registered = await register_saved_whatsapp_channel(
        binding,
        workspace_id=workspace_id,
        request_base_url=request_base_url,
        client=client,
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


async def register_saved_whatsapp_channel(
    binding: WhatsAppChannelBinding,
    *,
    workspace_id: str,
    request_base_url: str,
    client: Optional[Any] = None,
    expected_grant: tuple[str, str] | None = None,
) -> WhatsAppChannelBinding:
    """Register the webhook outside the transaction and guard the result write."""
    from app.agentive.clients.jvconnect_whatsapp import fetch_account, register_webhook

    async def register() -> tuple[str, str, str, str]:
        key = read_api_key(binding)
        if not key:
            return "", "", "", "Could not read the stored API key"
        account = await fetch_account(binding.jvconnect_base_url, key, client=client)
        if not account.ok:
            error = _safe_provider_error(
                account.error or "Could not read the WhatsApp account", key
            )
            return "", "", "", error
        raw_phone = account.data.get("phone_number_id")
        if not isinstance(raw_phone, str) or not raw_phone.strip():
            return "", "", "", "WhatsApp account did not include a phone number id"
        raw_display = account.data.get("display_phone_number")
        display = raw_display.strip() if isinstance(raw_display, str) else ""
        callback = _public_callback_url(request_base_url)
        if not callback:
            return "", "", "", "PUBLIC_API_BASE_URL is required"
        result = await register_webhook(
            binding.jvconnect_base_url, key, callback, client=client
        )
        if not result.ok:
            error = _safe_provider_error(
                result.error or "Webhook registration failed", key
            )
            return "", "", "", error
        secret = result.data.get("webhook_secret")
        if not isinstance(secret, str) or not secret:
            return "", "", "", "Webhook registration did not return a secret"
        return raw_phone.strip(), display, secret, ""

    phone, display, secret, error = await register()
    async with _workspace_line_write(workspace_id):
        current = await binding_for_workspace(workspace_id)
        if (
            current is None
            or current.id != binding.id
            or current.api_key_ciphertext != binding.api_key_ciphertext
        ):
            raise ResourceConflictError(
                message="The workspace WhatsApp connection changed during registration."
            )
        if expected_grant is not None:
            grant = await workspace_line_grant(workspace_id, current.id)
            if (
                grant is None
                or (grant.id, workspace_line_epoch(current, grant)) != expected_grant
            ):
                raise ResourceConflictError(
                    "WhatsApp grant changed during registration"
                )
        current.enabled = not error
        current.last_error = error
        if error:
            current.updated_at = utc_now_iso()
            await current.save()
        else:
            current.phone_number_id = phone
            current.display_phone = display
            await store_webhook_secret(current, secret)
    await current.set_context(get_default_context())
    return current


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
    user_id: str,
    workspace_id: str,
    binding_id: str,
    *,
    expected_grant: tuple[str, str] | None = None,
) -> WhatsAppChannelBinding:
    """Remove this workspace's grant. The last grant disables the line."""
    await _require_line_admin(user_id, workspace_id)
    async with _workspace_line_write(workspace_id):
        binding = await WhatsAppChannelBinding.get(binding_id)
        granted = [] if binding is None else await granted_workspace_ids(binding)
        if binding is None or workspace_id not in granted:
            raise ResourceNotFoundError(message="WhatsApp channel is not configured")
        if expected_grant is not None:
            grant = await workspace_line_grant(workspace_id, binding_id)
            if (
                grant is None
                or (grant.id, workspace_line_epoch(binding, grant)) != expected_grant
            ):
                raise ResourceConflictError(
                    "WhatsApp grant changed. Refresh and try again."
                )
        workspace = await _workspace_node(workspace_id)
        await _revoke_grant(workspace, binding)
        if not await granted_workspace_ids(binding):
            binding.enabled = False
            binding.updated_at = utc_now_iso()
            await binding.save()
        else:
            binding = await WhatsAppChannelBinding.get(binding_id) or binding
    await binding.set_context(get_default_context())
    return binding
