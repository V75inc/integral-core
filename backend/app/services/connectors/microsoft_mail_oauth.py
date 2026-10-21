"""Microsoft Entra OAuth for the Microsoft 365 mail stdio connector.

The access token is handed to ``@softeria/ms-365-mcp-server`` as
``MS365_MCP_OAUTH_TOKEN``. That server does not refresh a bring-your-own
token, so the refresh token stays on the connector and Integral mints a new
access token before a later spawn. Token values are never logged.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlencode, urlparse

import httpx

from app.api.errors import ConnectorAuthError
from app.services.connectors.quickbooks_oauth import _build_state

logger = logging.getLogger(__name__)

SCOPES = "offline_access User.Read Mail.Read Mail.Read.Shared"
CATALOG_SLUG = "microsoft_365_mail"
_TOKEN_SKEW = timedelta(minutes=5)


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


def _auth_field(auth_state: Dict[str, Any], name: str, env_key: str) -> str:
    """Read a credential from the connector row, then from the spawn env."""
    direct = str(auth_state.get(name) or "").strip()
    if direct:
        return direct
    env = auth_state.get("env")
    if isinstance(env, dict):
        return str(env.get(env_key) or "").strip()
    return ""


def _parse_expiry(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def access_token_is_fresh(
    auth_state: Dict[str, Any], *, now: Optional[datetime] = None
) -> bool:
    """True when the stored access token is present and outside the refresh skew.

    A missing expiry is stale. The first mail mount stored the access token
    without ``access_token_expires_at``, and Graph rejects that token after
    about an hour.
    """
    env = auth_state.get("env")
    token = ""
    if isinstance(env, dict):
        token = str(env.get("MS365_MCP_OAUTH_TOKEN") or "").strip()
    if not token:
        return False
    expires_at = _parse_expiry(auth_state.get("access_token_expires_at"))
    if expires_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    return expires_at > current + _TOKEN_SKEW


def store_refreshed_tokens(
    auth_state: Dict[str, Any],
    payload: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Copy ``auth_state`` with the new access token in the spawn env."""
    updated = dict(auth_state)
    env = dict(updated.get("env") or {})
    env["MS365_MCP_OAUTH_TOKEN"] = str(payload.get("access_token") or "")
    updated["env"] = env
    rotated = str(payload.get("refresh_token") or "").strip()
    if rotated:
        updated["refresh_token"] = rotated
    expires_in = int(payload.get("expires_in") or 3600)
    current = now or datetime.now(timezone.utc)
    updated["access_token_expires_at"] = (
        current + timedelta(seconds=max(expires_in, 60))
    ).isoformat()
    updated.pop("reauth_required", None)
    return updated


async def refresh_access_token(
    *,
    tenant: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    http_client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """Mint a new access token. Raises ConnectorAuthError on a rejected refresh.

    A 400 or 401 marks ``reauth_required``. Other statuses stay retryable so a
    transient Entra failure does not ask the person to connect again.
    """
    token_url = f"https://login.microsoftonline.com/{tenant.strip()}/oauth2/v2.0/token"
    body = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
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
            "microsoft_mail_oauth: refresh returned status=%s",
            response.status_code,
        )
        raise ConnectorAuthError(
            message=f"Microsoft token endpoint returned {response.status_code}",
            details={
                "status_code": response.status_code,
                "reauth_required": response.status_code in {400, 401},
            },
        )
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise ConnectorAuthError(
            message="Microsoft token response did not include an access token",
            details={"reauth_required": True},
        )
    return payload


async def refreshed_auth_state(
    auth_state: Dict[str, Any],
    *,
    http_client: Optional[httpx.AsyncClient] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Return auth state whose spawn env carries a usable access token."""
    if str(auth_state.get("catalog_slug") or "") != CATALOG_SLUG:
        return auth_state
    if not force and access_token_is_fresh(auth_state):
        return auth_state
    # The refresh token stays on the connector. It is not a spawn env var.
    refresh_token = str(auth_state.get("refresh_token") or "").strip()
    client_id = _auth_field(auth_state, "client_id", "MS365_MCP_CLIENT_ID")
    client_secret = _auth_field(auth_state, "client_secret", "MS365_MCP_CLIENT_SECRET")
    tenant = _auth_field(auth_state, "tenant_id", "MS365_MCP_TENANT_ID")
    if not refresh_token or not client_id or not client_secret or not tenant:
        raise ConnectorAuthError(
            message="Microsoft 365 mail is missing credentials for token refresh",
            details={"reauth_required": True},
        )
    payload = await refresh_access_token(
        tenant=tenant,
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
        http_client=http_client,
    )
    return store_refreshed_tokens(auth_state, payload)


__all__ = [
    "CATALOG_SLUG",
    "SCOPES",
    "access_token_is_fresh",
    "build_consent_url",
    "consent_url",
    "exchange_code",
    "refresh_access_token",
    "refreshed_auth_state",
    "spawn_env",
    "store_refreshed_tokens",
]
