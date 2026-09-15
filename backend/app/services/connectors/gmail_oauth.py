"""Google OAuth2 helpers for the Gmail connector.

Persistence-free helpers for the Gmail OAuth authorization-code flow.
Mirrors the QuickBooks OAuth helper shape (state signing + TTL +
hmac.compare_digest), but issues against Google's endpoints and
requests the ``gmail.readonly`` scope ONLY (scope minimization).

Tokens never appear in log lines.

Deployment secrets:
- ``GMAIL_OAUTH_CLIENT_ID``
- ``GMAIL_OAUTH_CLIENT_SECRET``
- ``GMAIL_OAUTH_REDIRECT_URI``
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

GOOGLE_CONSENT_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

# Read-only scope is the ONLY scope this phase requests. No send / modify /
# settings scope, no openid / profile / email scopes. Adding any additional
# scope requires an explicit follow-up plan + Decision Record.
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

_STATE_TTL_SECONDS = 600  # 10 minutes


def _signing_key() -> bytes:
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
    nonce = secrets.token_urlsafe(8)
    expires_at = _now() + _STATE_TTL_SECONDS
    payload = f"{user_id}:{nonce}:{expires_at}"
    if connector_id:
        payload = f"{payload}:{connector_id}"
    return _b64_encode(payload.encode("utf-8")) + "." + _sign_state(payload)


def verify_state(state: str, user_id: str) -> bool:
    """Validate a signed CSRF state token bound to user_id with TTL."""
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
    expected = _sign_state(payload)
    if not hmac.compare_digest(expected, signature):
        return False
    if expires_at < _now():
        return False
    if bound_user != user_id:
        return False
    return True


def connector_id_from_state(state: str) -> Optional[str]:
    """Extract the optional connector_id suffix from a signed Gmail state token."""
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
        settings.GMAIL_OAUTH_CLIENT_ID or os.getenv("GMAIL_OAUTH_CLIENT_ID") or ""
    ).strip()


def _env_client_secret() -> str:
    from app.config import settings

    return (
        settings.GMAIL_OAUTH_CLIENT_SECRET
        or os.getenv("GMAIL_OAUTH_CLIENT_SECRET")
        or ""
    ).strip()


def build_consent_url(
    user_id: str,
    *,
    redirect_uri: Optional[str] = None,
    client_id: Optional[str] = None,
    connector_id: Optional[str] = None,
) -> Tuple[str, str]:
    """Build Google's consent URL + return the signed state token."""
    from app.config import settings

    resolved_client_id = (client_id or "").strip() or _env_client_id()
    if not resolved_client_id:
        raise ConnectorAuthError(
            message=(
                "Gmail Client ID is required. Enter it in the install form, "
                "or set GMAIL_OAUTH_CLIENT_ID on the server."
            ),
            details={
                "reauth_required": False,
                "config_missing": "GMAIL_OAUTH_CLIENT_ID",
            },
        )
    effective_redirect = (
        redirect_uri
        or settings.GMAIL_OAUTH_REDIRECT_URI
        or os.getenv("GMAIL_OAUTH_REDIRECT_URI")
        or ""
    )
    state = _build_state(user_id, connector_id=connector_id)
    params = {
        "client_id": resolved_client_id,
        "scope": GMAIL_SCOPE,
        "redirect_uri": effective_redirect,
        "response_type": "code",
        "state": state,
        # access_type=offline so Google returns a refresh_token.
        "access_type": "offline",
        # prompt=consent forces a fresh refresh_token on re-consent.
        "prompt": "consent",
    }
    url = f"{GOOGLE_CONSENT_URL}?{urlencode(params)}"
    logger.info("gmail_oauth: built consent URL for user_id=%s", user_id)
    return url, state


async def _post_to_token_endpoint(
    *,
    body: Dict[str, str],
    http_client: Optional[httpx.AsyncClient] = None,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
) -> Dict[str, Any]:
    resolved_id = (client_id or "").strip() or _env_client_id()
    resolved_secret = (client_secret or "").strip() or _env_client_secret()
    if not resolved_id or not resolved_secret:
        raise ConnectorAuthError(
            message=(
                "Gmail client credentials are required. Enter Client ID and "
                "Client secret in the install form, or set "
                "GMAIL_OAUTH_CLIENT_ID / GMAIL_OAUTH_CLIENT_SECRET on the server."
            ),
            details={
                "reauth_required": False,
                "config_missing": (
                    "GMAIL_OAUTH_CLIENT_ID"
                    if not resolved_id
                    else "GMAIL_OAUTH_CLIENT_SECRET"
                ),
            },
        )
    full_body = {**body, "client_id": resolved_id, "client_secret": resolved_secret}
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    owns = http_client is None
    client = http_client or httpx.AsyncClient(timeout=15.0)
    try:
        resp = await client.post(
            GOOGLE_TOKEN_ENDPOINT, headers=headers, content=urlencode(full_body)
        )
    finally:
        if owns:
            await client.aclose()
    if resp.status_code != 200:
        reauth_required = False
        try:
            err_body = resp.json()
            err_code = (err_body or {}).get("error") or ""
            if err_code == "invalid_grant":
                reauth_required = True
            logger.warning(
                "gmail_oauth: token endpoint returned status=%s error=%s",
                resp.status_code,
                err_code,
            )
        except Exception:
            logger.warning(
                "gmail_oauth: token endpoint returned status=%s (no JSON body)",
                resp.status_code,
            )
        raise ConnectorAuthError(
            message=f"Gmail token endpoint returned {resp.status_code}",
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
    """Exchange an authorization-code for access + refresh tokens."""
    from app.config import settings

    effective_redirect = (
        redirect_uri
        or settings.GMAIL_OAUTH_REDIRECT_URI
        or os.getenv("GMAIL_OAUTH_REDIRECT_URI")
        or ""
    )
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": effective_redirect,
    }
    logger.info("gmail_oauth: exchanging authorization code")
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
    """Refresh the access_token.

    Google does NOT always return a new refresh_token on refresh — the
    existing refresh_token MUST be retained. Caller layers that detail
    on top of the parsed response.

    Raises ``ConnectorAuthError`` (with ``reauth_required=True`` on
    invalid_grant) so the connector can flag itself for re-auth.
    """
    body = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    logger.info("gmail_oauth: refreshing access token")
    return await _post_to_token_endpoint(
        body=body,
        http_client=http_client,
        client_id=client_id,
        client_secret=client_secret,
    )
