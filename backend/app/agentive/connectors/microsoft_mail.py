"""Keep the Microsoft 365 mail stdio spawn on a live access token.

``@softeria/ms-365-mcp-server`` reads ``MS365_MCP_OAUTH_TOKEN`` and does not
refresh it. Graph access tokens last about an hour, so each spawn refreshes
when the stored expiry is missing or inside the skew window. The refresh
token stays on the connector and is persisted before the tool call, because
Entra can rotate it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

_LOCKS: Dict[str, asyncio.Lock] = {}


def _lock_for(connector_id: str) -> asyncio.Lock:
    lock = _LOCKS.get(connector_id)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[connector_id] = lock
    return lock


def _material_changed(before: Dict[str, Any], after: Dict[str, Any]) -> bool:
    before_env = before.get("env") if isinstance(before.get("env"), dict) else {}
    after_env = after.get("env") if isinstance(after.get("env"), dict) else {}
    return (
        before_env.get("MS365_MCP_OAUTH_TOKEN")
        != after_env.get("MS365_MCP_OAUTH_TOKEN")
        or before.get("refresh_token") != after.get("refresh_token")
        or before.get("access_token_expires_at") != after.get("access_token_expires_at")
        or before.get("reauth_required") != after.get("reauth_required")
    )


async def ensure_fresh_access_token(
    connector: Any,
    *,
    http_client: Any = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Decrypt, refresh when needed, and return the auth state for this spawn.

    Non-mail connectors are returned decrypted and unchanged. A rejected
    refresh sets ``reauth_required`` on the row and re-raises.
    """
    from app.agentive.connectors.mcp_adapter import plain_auth_state, store_auth_state
    from app.api.errors import ConnectorAuthError
    from app.services.connectors.microsoft_mail_oauth import (
        CATALOG_SLUG,
        refreshed_auth_state,
    )

    auth = plain_auth_state(connector)
    if str(auth.get("catalog_slug") or "") != CATALOG_SLUG:
        return auth
    connector_id = str(getattr(connector, "id", "") or "") or str(id(connector))
    async with _lock_for(connector_id):
        auth = plain_auth_state(connector)
        try:
            updated = await refreshed_auth_state(
                auth, http_client=http_client, force=force
            )
        except ConnectorAuthError as exc:
            details = getattr(exc, "details", None) or {}
            if details.get("reauth_required"):
                marked = dict(auth)
                marked["reauth_required"] = True
                store_auth_state(connector, marked)
                await connector.save()
            raise
        if _material_changed(auth, updated):
            store_auth_state(connector, updated)
            await connector.save()
            logger.info(
                "microsoft_mail: refreshed access token for connector %s",
                connector_id,
            )
        return updated
