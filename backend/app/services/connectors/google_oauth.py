"""Google Workspace OAuth2 helpers for native Drive / Sheets connectors.

Persistence-free helpers for the Google authorization-code flow, mirroring
the Gmail helper shape (``app/services/connectors/gmail_oauth.py``: state
signing + TTL + hmac.compare_digest) but parameterized by scope set so one
Google OAuth app serves Drive + Sheets (+ Gmail fallback).

Scope policy (read + write, per product decision):
- Drive native: ``drive`` (full) + ``drive.file``.
- Sheets native: ``spreadsheets`` (full) + ``drive.file`` (spreadsheet
  creation / lookup touches Drive metadata).

Full scopes subsume their ``*.readonly`` counterparts; requesting both is
redundant. These are sensitive/restricted Google scopes — the OAuth client
needs matching verification before production use.

Tokens never appear in log lines.

Deployment secrets (shared Google OAuth app; falls back to the Gmail app):
- ``GOOGLE_OAUTH_CLIENT_ID`` / ``GOOGLE_OAUTH_CLIENT_SECRET``
- ``GMAIL_OAUTH_CLIENT_ID`` / ``GMAIL_OAUTH_CLIENT_SECRET`` (fallback)
- ``GOOGLE_WORKSPACE_OAUTH_REDIRECT_URI`` (fallback: ``GMAIL_OAUTH_REDIRECT_URI``)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import httpx

from app.api.errors import ConnectorAuthError
from app.config import settings

logger = logging.getLogger(__name__)

GOOGLE_CONSENT_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

# Full-access scopes (read + write). One entry per native connector slug.
# ``drive`` (full) subsumes drive.readonly/drive.file; ``spreadsheets``
# (full) subsumes spreadsheets.readonly. Sheets keeps drive.readonly (list
# / discover the user's spreadsheets — drive.file alone only sees
# app-created files) + drive.file (operate on files it creates).
DRIVE_NATIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
]
SHEETS_NATIVE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]

NATIVE_SLUG_SCOPES: Dict[str, List[str]] = {
    "drive_native": DRIVE_NATIVE_SCOPES,
    "sheets_native": SHEETS_NATIVE_SCOPES,
}

_STATE_TTL_SECONDS = 600  # 10 minutes


def _signing_key() -> bytes:
    # Same trust root as JWTs + the Gmail helper. No fallback: an empty key
    # is a deployment error (a forgeable CSRF state token otherwise).
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


def _build_state(
    user_id: str,
    provider: str,
    connector_id: Optional[str] = None,
) -> str:
    """Signed CSRF state binding user + provider (+ optional connector id).

    The ``provider`` segment (``drive_native`` / ``sheets_native``) lets one
    callback endpoint serve both connectors — the state token, not a URL
    path, disambiguates which connector the code belongs to.
    """
    nonce = secrets.token_urlsafe(8)
    expires_at = _now() + _STATE_TTL_SECONDS
    payload = f"{user_id}:{provider}:{nonce}:{expires_at}"
    if connector_id:
        payload = f"{payload}:{connector_id}"
    return _b64_encode(payload.encode("utf-8")) + "." + _sign_state(payload)


def _split_state_payload(state: str) -> Optional[List[str]]:
    if not state or "." not in state:
        return None
    try:
        payload_b64, signature = state.rsplit(".", 1)
        payload = _b64_decode(payload_b64).decode("utf-8")
    except ValueError:
        return None
    expected = _sign_state(payload)
    if not hmac.compare_digest(expected, signature):
        return None
    return payload.split(":")


def verify_state(state: str, user_id: str, provider: str) -> bool:
    """Validate a signed CSRF state token bound to (user_id, provider) + TTL."""
    parts = _split_state_payload(state)
    if parts is None or len(parts) < 4:
        return False
    bound_user, bound_provider = parts[0], parts[1]
    try:
        expires_at = int(parts[3])
    except ValueError:
        return False
    if expires_at < _now():
        return False
    return bound_user == user_id and bound_provider == provider


def provider_from_state(state: str) -> Optional[str]:
    """Extract the provider segment from a signed state token (or None)."""
    parts = _split_state_payload(state)
    if parts is None or len(parts) < 2:
        return None
    return parts[1] or None


def connector_id_from_state(state: str) -> Optional[str]:
    """Extract the optional connector_id suffix from a signed state token."""
    parts = _split_state_payload(state)
    if parts is None or len(parts) < 5:
        return None
    cid = ":".join(parts[4:]).strip()
    return cid or None


def _env_client_id() -> str:
    return (
        getattr(settings, "GOOGLE_OAUTH_CLIENT_ID", None)
        or os.getenv("GOOGLE_OAUTH_CLIENT_ID")
        or getattr(settings, "GMAIL_OAUTH_CLIENT_ID", None)
        or os.getenv("GMAIL_OAUTH_CLIENT_ID")
        or ""
    ).strip()


def _env_client_secret() -> str:
    return (
        getattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", None)
        or os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
        or getattr(settings, "GMAIL_OAUTH_CLIENT_SECRET", None)
        or os.getenv("GMAIL_OAUTH_CLIENT_SECRET")
        or ""
    ).strip()


def scopes_for_provider(provider: str) -> List[str]:
    """Full read+write scopes for a native provider slug (KeyError if unknown)."""
    return list(NATIVE_SLUG_SCOPES[provider])


def effective_redirect_uri() -> str:
    """Effective OAuth redirect URI for native Google Workspace connectors."""
    return (
        getattr(settings, "GOOGLE_WORKSPACE_OAUTH_REDIRECT_URI", None)
        or os.getenv("GOOGLE_WORKSPACE_OAUTH_REDIRECT_URI")
        or getattr(settings, "GMAIL_OAUTH_REDIRECT_URI", None)
        or os.getenv("GMAIL_OAUTH_REDIRECT_URI")
        or ""
    )


def build_consent_url(
    user_id: str,
    provider: str,
    *,
    redirect_uri: Optional[str] = None,
    client_id: Optional[str] = None,
    connector_id: Optional[str] = None,
) -> Tuple[str, str]:
    """Build Google's consent URL for ``provider`` + return the signed state."""
    resolved_client_id = (client_id or "").strip() or _env_client_id()
    if not resolved_client_id:
        raise ConnectorAuthError(
            message=(
                "Google Client ID is required. Enter it in the install form, "
                "or set GOOGLE_OAUTH_CLIENT_ID on the server."
            ),
            details={
                "reauth_required": False,
                "config_missing": "GOOGLE_OAUTH_CLIENT_ID",
            },
        )
    try:
        scopes = scopes_for_provider(provider)
    except KeyError:
        raise ConnectorAuthError(
            message=f"Unknown Google Workspace provider {provider!r}",
            details={"reauth_required": False, "provider": provider},
        )
    effective_redirect = (redirect_uri or "").strip() or effective_redirect_uri()
    state = _build_state(user_id, provider, connector_id=connector_id)
    params = {
        "client_id": resolved_client_id,
        "scope": " ".join(scopes),
        "redirect_uri": effective_redirect,
        "response_type": "code",
        "state": state,
        # access_type=offline so Google returns a refresh_token.
        "access_type": "offline",
        # prompt=consent forces a fresh refresh_token on re-consent.
        "prompt": "consent",
    }
    url = f"{GOOGLE_CONSENT_URL}?{urlencode(params)}"
    logger.info("google_oauth: built consent URL provider=%s", provider)
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
                "Google client credentials are required. Enter Client ID and "
                "Client secret in the install form, or set "
                "GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET on the server."
            ),
            details={
                "reauth_required": False,
                "config_missing": (
                    "GOOGLE_OAUTH_CLIENT_ID"
                    if not resolved_id
                    else "GOOGLE_OAUTH_CLIENT_SECRET"
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
                "google_oauth: token endpoint returned status=%s error=%s",
                resp.status_code,
                err_code,
            )
        except Exception:
            logger.warning(
                "google_oauth: token endpoint returned status=%s (no JSON body)",
                resp.status_code,
            )
        raise ConnectorAuthError(
            message=f"Google token endpoint returned {resp.status_code}",
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
    effective_redirect = (redirect_uri or "").strip() or effective_redirect_uri()
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": effective_redirect,
    }
    logger.info("google_oauth: exchanging authorization code")
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
    caller MUST retain the existing refresh_token when the response omits
    one. Raises ``ConnectorAuthError`` (``reauth_required=True`` on
    invalid_grant) so the connector can flag itself for re-auth.
    """
    body = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    logger.info("google_oauth: refreshing access token")
    return await _post_to_token_endpoint(
        body=body,
        http_client=http_client,
        client_id=client_id,
        client_secret=client_secret,
    )


_REAUTH_REQUIRED_KEY = "reauth_required"
_TOKEN_SKEW_SECONDS = 300


async def ensure_fresh_token(
    connector: Any,
    *,
    http_client: Optional[httpx.AsyncClient] = None,
) -> bool:
    """Refresh ``connector``'s Google access token when expired/near-expiry.

    Shared by the native Drive / Sheets connectors (and their tool proxy).
    Mutates + saves ``connector.auth_state`` in place. Returns True when a
    usable access token is present afterwards, False when re-authorization
    is required (missing refresh token or ``invalid_grant``) — the caller
    must fail closed with a re-auth message, never retry the API call.
    """
    from datetime import datetime, timedelta, timezone

    auth_state: Dict[str, Any] = dict(getattr(connector, "auth_state", {}) or {})

    def _parse_iso(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None

    expires_at = _parse_iso(auth_state.get("access_token_expires_at"))
    skew_threshold = datetime.now(timezone.utc) + timedelta(seconds=_TOKEN_SKEW_SECONDS)
    if expires_at is not None and expires_at > skew_threshold:
        return True
    refresh_token = auth_state.get("refresh_token") or ""
    if not refresh_token:
        logger.warning(
            "google_oauth ensure_fresh_token: no refresh_token (connector=%s); reauth required",
            getattr(connector, "id", "<unknown>"),
        )
        auth_state[_REAUTH_REQUIRED_KEY] = True
        connector.auth_state = auth_state
        await connector.save()
        return False
    try:
        refreshed = await refresh_tokens(
            refresh_token,
            http_client=http_client,
            client_id=auth_state.get("client_id") or None,
            client_secret=auth_state.get("client_secret") or None,
        )
    except ConnectorAuthError as exc:
        details = getattr(exc, "details", None) or {}
        if details.get("reauth_required"):
            auth_state[_REAUTH_REQUIRED_KEY] = True
            connector.auth_state = auth_state
            await connector.save()
        return False
    new_access = refreshed.get("access_token") or ""
    # Google MAY omit refresh_token on refresh; preserve the existing one.
    new_refresh = refreshed.get("refresh_token") or refresh_token
    expires_in = int(refreshed.get("expires_in") or 3600)
    auth_state["access_token"] = new_access
    auth_state["refresh_token"] = new_refresh
    auth_state["access_token_expires_at"] = (
        datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    ).isoformat()
    auth_state.pop(_REAUTH_REQUIRED_KEY, None)
    connector.auth_state = auth_state
    await connector.save()
    logger.info(
        "google_oauth ensure_fresh_token: rotated access_token for connector %s",
        getattr(connector, "id", "<unknown>"),
    )
    return True
