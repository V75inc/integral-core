"""Microsoft Entra OAuth for the Microsoft 365 mail stdio connector.

The access token is handed to ``@softeria/ms-365-mcp-server`` as
``MS365_MCP_OAUTH_TOKEN``. That server does not refresh a bring-your-own
token, so the refresh token stays on the connector and a later spawn must
mint a new access token. Token values are never logged.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from urllib.parse import urlencode, urlparse

import httpx

from app.api.errors import ConnectorAuthError
from app.services.connectors.quickbooks_oauth import _build_state

logger = logging.getLogger(__name__)

SCOPES = "offline_access User.Read Mail.Read Mail.Read.Shared"
CATALOG_SLUG = "microsoft_365_mail"


def absolute_redirect(value: str) -> str:
    """Reject a blank or relative redirect before it reaches Entra."""
    text = (value or "").strip()
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(
            "MICROSOFT_MAIL_REDIRECT_URI must be an absolute http(s) URI, "
            "for example http://localhost:9006/connectors/microsoft-mail/callback"
        )
    return text


def consent_url(
    *,
    tenant: str,
    client_id: str,
    redirect_uri: str,
    state: str,
) -> str:
    """Authorization URL for one Entra tenant. Mail.Send is not included."""
    tenant_id = (tenant or "").strip()
    app_id = (client_id or "").strip()
    callback = absolute_redirect(redirect_uri)
    if not tenant_id or not app_id:
        raise ValueError("tenant_id and client_id are required")
    if "Mail.Send" in SCOPES:
        raise ValueError("Mail.Send is not a mail-read scope")
    params = {
        "client_id": app_id,
        "response_type": "code",
        "redirect_uri": callback,
        "response_mode": "query",
        "scope": SCOPES,
        "state": state,
    }
    return (
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize?"
        f"{urlencode(params)}"
    )


def redirect_uri() -> str:
    """Callback the browser returns to. Defaults to the local app origin."""
    from app.config import settings

    return absolute_redirect(settings.MICROSOFT_MAIL_REDIRECT_URI)


def build_consent_url(
    user_id: str,
    *,
    tenant: str,
    client_id: str,
    connector_id: Optional[str] = None,
    redirect: Optional[str] = None,
) -> tuple[str, str]:
    """Signed consent URL bound to the installing user."""
    state = _build_state(user_id, connector_id=connector_id)
    url = consent_url(
        tenant=tenant,
        client_id=client_id,
        redirect_uri=redirect if redirect is not None else redirect_uri(),
        state=state,
    )
    logger.info("microsoft_mail_oauth: built consent URL for user_id=%s", user_id)
    return url, state


def spawn_env(
    *,
    access_token: str,
    client_id: str,
    client_secret: str,
    tenant_id: str,
) -> Dict[str, str]:
    """Environment for one stdio spawn. The refresh token is not included."""
    raw = {
        "MS365_MCP_OAUTH_TOKEN": access_token,
        "MS365_MCP_CLIENT_ID": client_id,
        "MS365_MCP_CLIENT_SECRET": client_secret,
        "MS365_MCP_TENANT_ID": tenant_id,
    }
    return {key: value for key, value in raw.items() if str(value or "").strip()}


async def exchange_code(
    code: str,
    *,
    tenant: str,
    client_id: str,
    client_secret: str,
    redirect: Optional[str] = None,
    http_client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """Exchange an authorization code. Raises ConnectorAuthError on non-200."""
    token_url = f"https://login.microsoftonline.com/{tenant.strip()}/oauth2/v2.0/token"
    chosen = (redirect or "").strip()
    callback = absolute_redirect(chosen) if chosen else redirect_uri()
    body = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": callback,
        "scope": SCOPES,
    }
    client_ctx = http_client or httpx.AsyncClient(timeout=15.0)
    try:
        response = await client_ctx.post(token_url, data=body)
    finally:
        if http_client is None:
            await client_ctx.aclose()
    if response.status_code != 200:
        logger.warning(
            "microsoft_mail_oauth: token endpoint returned status=%s",
            response.status_code,
        )
        raise ConnectorAuthError(
            message=f"Microsoft token endpoint returned {response.status_code}",
            details={"status_code": response.status_code, "reauth_required": True},
        )
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise ConnectorAuthError(
            message="Microsoft token response did not include an access token",
            details={"reauth_required": True},
        )
    return payload


__all__ = [
    "CATALOG_SLUG",
    "SCOPES",
    "build_consent_url",
    "consent_url",
    "exchange_code",
    "spawn_env",
]
