"""Intuit QuickBooks Online OAuth2 helpers (connector framework).

Persistence-free helpers for the QuickBooks Online authorization-code flow:

- ``build_consent_url(user_id)`` — build Intuit's consent URL with a signed,
  short-TTL CSRF ``state`` token bound to the calling user. Returns
  ``(url, state)``.
- ``verify_state(state, user_id)`` — validate signature + TTL + user binding.
- ``exchange_code(code, redirect_uri=None)`` — POST ``grant_type=
  authorization_code`` to Intuit's token endpoint.
- ``refresh_tokens(refresh_token)`` — POST ``grant_type=refresh_token``,
  returning the new access token AND the *rotated* refresh token.

Token values are NEVER logged. Every log line emits only non-secret context
(operation name + status code).

The Intuit ``client_id`` / ``client_secret`` default to deployment env vars
(``QUICKBOOKS_CLIENT_ID``, ``QUICKBOOKS_CLIENT_SECRET``). Catalog install
may persist operator-supplied values on ``Connector.auth_state``; those
override env for that connector. ``client_secret`` is redacted on the wire.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

import httpx

from app.api.errors import ConnectorAuthError
from app.config import settings

logger = logging.getLogger(__name__)

QBO_CONSENT_URL = "https://appcenter.intuit.com/connect/oauth2"
QBO_TOKEN_ENDPOINT = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"

# Accounting scope only — payroll scope is out of scope per the master
# plan §9 Q2 resolution. NEVER add ``com.intuit.quickbooks.payroll`` or
# ``openid`` / ``profile`` scopes here; if a future phase needs them, gate
# the addition behind an explicit plan + Decision Record.
QBO_SCOPE = "com.intuit.quickbooks.accounting"

# CSRF state token TTL (seconds). Short — the consent flow round-trips
# through Intuit's UI in seconds, not minutes.
_STATE_TTL_SECONDS = 600  # 10 minutes


def _signing_key() -> bytes:
    """Derive the HMAC signing key for state tokens.

    The state token is signed by HMAC-SHA256 over the deployment's
    ``SECRET_KEY``. Compromise of the SECRET_KEY would already imply a
    larger break; the state token shares the same trust root as JWTs.
    """
    # ``settings.SECRET_KEY`` reads the canonical ``JVSPATIAL_JWT_SECRET_KEY``
    # (the same trust root as JWTs). The previous ``os.getenv("SECRET_KEY")``
    # lookup silently fell back to a hard-coded dev secret in production,
    # where only ``JVSPATIAL_JWT_SECRET_KEY`` is set — making the CSRF state
    # token forgeable. No fallback: an empty key is a deployment error.
    secret = settings.SECRET_KEY
    if not secret:
        raise RuntimeError(
            "JVSPATIAL_JWT_SECRET_KEY is not configured — cannot sign OAuth state"
        )
    return hashlib.sha256(secret.encode("utf-8")).digest()


def _now() -> int:
    return int(time.time())


def _b64_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64_decode(s: str) -> bytes:
    padded = s + "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _sign_state(payload: str) -> str:
    mac = hmac.new(_signing_key(), payload.encode("utf-8"), hashlib.sha256).digest()
    return _b64_encode(mac)


def _build_state(user_id: str, connector_id: Optional[str] = None) -> str:
    """Build a signed CSRF state token bound to ``user_id`` with a short TTL."""
    nonce = secrets.token_urlsafe(8)
    expires_at = _now() + _STATE_TTL_SECONDS
    payload = f"{user_id}:{nonce}:{expires_at}"
    if connector_id:
        payload = f"{payload}:{connector_id}"
    signature = _sign_state(payload)
    return _b64_encode(payload.encode("utf-8")) + "." + signature


def verify_state(state: str, user_id: str) -> bool:
    """Validate a CSRF state token.

    Returns True iff the signature is valid, the TTL has not expired, and
    the embedded user_id matches the caller. Constant-time comparison.
    """
    if not state or "." not in state:
        return False
    try:
        payload_b64, signature = state.rsplit(".", 1)
        payload = _b64_decode(payload_b64).decode("utf-8")
        parts = payload.split(":")
        if len(parts) < 3:
            return False
        bound_user = parts[0]
        expires_at_str = parts[2]
        expires_at = int(expires_at_str)
    except ValueError:
        return False
    expected_sig = _sign_state(payload)
    if not hmac.compare_digest(expected_sig, signature):
        return False
    if expires_at < _now():
        return False
    if bound_user != user_id:
        return False
    return True


def connector_id_from_state(state: str) -> Optional[str]:
    """Return the connector id embedded in a valid-looking state token, if any."""
    if not state or "." not in state:
        return None
    try:
        payload_b64, _signature = state.rsplit(".", 1)
        payload = _b64_decode(payload_b64).decode("utf-8")
        parts = payload.split(":")
        if len(parts) < 4:
            return None
        cid = ":".join(parts[3:]).strip()
        return cid or None
    except ValueError:
        return None


def _env_client_id() -> str:
    from app.config import settings

    return (
        settings.QUICKBOOKS_CLIENT_ID or os.getenv("QUICKBOOKS_CLIENT_ID") or ""
    ).strip()


def _env_client_secret() -> str:
    from app.config import settings

    return (
        settings.QUICKBOOKS_CLIENT_SECRET or os.getenv("QUICKBOOKS_CLIENT_SECRET") or ""
    ).strip()


def build_consent_url(
    user_id: str,
    *,
    redirect_uri: Optional[str] = None,
    client_id: Optional[str] = None,
    connector_id: Optional[str] = None,
) -> Tuple[str, str]:
    """Build Intuit's consent URL + return the signed state token.

    The caller (the authorize endpoint) returns the URL to the frontend,
    which opens it in a popup. On consent Intuit redirects to
    ``redirect_uri`` with ``code``, ``state``, ``realmId``. The callback
    endpoint validates ``state`` via ``verify_state`` before exchanging
    the code.

    ``client_id`` overrides the deployment env var when the operator
    supplied app credentials in the catalog install form.
    """
    from app.config import settings

    resolved_client_id = (client_id or "").strip() or _env_client_id()
    if not resolved_client_id:
        raise ConnectorAuthError(
            message=(
                "QuickBooks Client ID is required. Enter it in the install "
                "form, or set QUICKBOOKS_CLIENT_ID on the server."
            ),
            details={
                "reauth_required": False,
                "config_missing": "QUICKBOOKS_CLIENT_ID",
            },
        )
    effective_redirect = (
        redirect_uri
        or settings.QUICKBOOKS_REDIRECT_URI
        or os.getenv("QUICKBOOKS_REDIRECT_URI")
        or ""
    )
    state = _build_state(user_id, connector_id=connector_id)
    params = {
        "client_id": resolved_client_id,
        "scope": QBO_SCOPE,
        "redirect_uri": effective_redirect,
        "response_type": "code",
        "state": state,
    }
    url = f"{QBO_CONSENT_URL}?{urlencode(params)}"
    logger.info("quickbooks_oauth: built consent URL for user_id=%s", user_id)
    return url, state


def _basic_auth_header(
    *, client_id: Optional[str] = None, client_secret: Optional[str] = None
) -> str:
    resolved_id = (client_id or "").strip() or _env_client_id()
    resolved_secret = (client_secret or "").strip() or _env_client_secret()
    if not resolved_id or not resolved_secret:
        raise ConnectorAuthError(
            message=(
                "QuickBooks client credentials are required. Enter Client ID "
                "and Client secret in the install form, or set "
                "QUICKBOOKS_CLIENT_ID / QUICKBOOKS_CLIENT_SECRET on the server."
            ),
            details={
                "reauth_required": False,
                "config_missing": (
                    "QUICKBOOKS_CLIENT_ID"
                    if not resolved_id
                    else "QUICKBOOKS_CLIENT_SECRET"
                ),
            },
        )
    raw = f"{resolved_id}:{resolved_secret}".encode("ascii")
    return "Basic " + base64.b64encode(raw).decode("ascii")


async def _post_to_token_endpoint(
    *,
    body: Dict[str, str],
    http_client: Optional[httpx.AsyncClient] = None,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
) -> Dict[str, Any]:
    """POST form-urlencoded body to Intuit's token endpoint and parse JSON.

    Pass ``http_client`` in tests to inject ``httpx.MockTransport``; production
    callers leave it None and the helper builds its own client.
    """
    headers = {
        "Authorization": _basic_auth_header(
            client_id=client_id, client_secret=client_secret
        ),
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    client_ctx = http_client or httpx.AsyncClient(timeout=15.0)
    try:
        resp = await client_ctx.post(
            QBO_TOKEN_ENDPOINT, headers=headers, content=urlencode(body)
        )
    finally:
        if http_client is None:
            await client_ctx.aclose()
    if resp.status_code != 200:
        # Try to extract Intuit's error key for a precise re-auth signal.
        reauth_required = False
        try:
            err_body = resp.json()
            err_code = (err_body or {}).get("error") or ""
            if err_code == "invalid_grant":
                reauth_required = True
            logger.warning(
                "quickbooks_oauth: token endpoint returned status=%s error=%s",
                resp.status_code,
                err_code,
            )
        except Exception:
            logger.warning(
                "quickbooks_oauth: token endpoint returned status=%s (no JSON body)",
                resp.status_code,
            )
        raise ConnectorAuthError(
            message=f"QuickBooks token endpoint returned {resp.status_code}",
            details={
                "status_code": resp.status_code,
                "reauth_required": reauth_required,
            },
        )
    return resp.json()


async def exchange_code(
    code: str,
    *,
    redirect_uri: Optional[str] = None,
    http_client: Optional[httpx.AsyncClient] = None,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
) -> Dict[str, Any]:
    """Exchange an authorization-code for access + refresh tokens.

    Returns the parsed Intuit response carrying ``access_token``,
    ``refresh_token``, ``expires_in``, ``x_refresh_token_expires_in``,
    ``token_type``. Raises ``ConnectorAuthError`` on non-200.
    """
    from app.config import settings

    effective_redirect = (
        redirect_uri
        or settings.QUICKBOOKS_REDIRECT_URI
        or os.getenv("QUICKBOOKS_REDIRECT_URI")
        or ""
    )
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": effective_redirect,
    }
    logger.info("quickbooks_oauth: exchanging authorization code")
    return await _post_to_token_endpoint(
        body=body,
        http_client=http_client,
        client_id=client_id,
        client_secret=client_secret,
    )


async def refresh_tokens(
    refresh_token: str,
    *,
    http_client: Optional[httpx.AsyncClient] = None,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
) -> Dict[str, Any]:
    """Refresh access + refresh tokens.

    Intuit ROTATES the refresh token on every refresh — the caller MUST
    persist the new ``refresh_token`` from the response before the next
    API call. Returns the parsed response carrying the new
    ``access_token``, the rotated ``refresh_token``, and refreshed
    ``expires_in`` / ``x_refresh_token_expires_in``.

    On HTTP 400 with ``error=invalid_grant`` (expired or revoked refresh
    token), raises ``ConnectorAuthError`` with
    ``details.reauth_required=True`` so callers can flag the connector
    for re-authorization without crashing the scheduler tick.
    """
    body = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    logger.info("quickbooks_oauth: refreshing tokens (rotation expected)")
    return await _post_to_token_endpoint(
        body=body,
        http_client=http_client,
        client_id=client_id,
        client_secret=client_secret,
    )
