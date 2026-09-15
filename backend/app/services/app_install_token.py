"""HMAC-signed install_token mint + verify for App install pause-until-submit.

Phase 10 Plan 10-05 (APP-LIFECYCLE-01 + APP-SETTINGS-01).

Resolves Architectural Decision 4 (Option A): the install transaction pauses
at step 9 (settings form) and returns a 202 + ``install_token``. The client
collects user-submitted settings, then POSTs them back with the token to
``/api/apps/{app_id}/install/settings`` to resume the transaction. This
module owns the token lifecycle:

- ``issue_install_token(app_id, ttl_hours=None)`` — mint a token tied to a
  specific App row, with a configurable TTL (default ``APP_INSTALL_TOKEN_TTL_HOURS``
  env var, default 1.0 hours).
- ``verify_install_token(token, expected_app_id)`` — verify signature, app_id
  match, and expiry. Returns the parsed payload on success, raises a
  structured error on failure.

Token format: ``base64url(payload_json) + "." + base64url(hmac_digest)``.
Payload: ``{"app_id": str, "issued_at": int, "expires_at": int, "nonce": str}``.

Signed with ``SECRET_KEY`` (jvspatial canonical signing key — single source of
truth for HMAC operations across this codebase). Constant-time comparison
via ``hmac.compare_digest``.

Single-use enforcement is the caller's responsibility: the lifecycle service
checks ``App.lifecycle_state == "awaiting_settings"`` before honoring a
token; once the App transitions to ``active``, a replayed token observes the
state mismatch and is rejected via ``AppLifecycleStateError``. The token
itself does not carry a one-time-use flag — that would require persisting
nonces, and the state-check approach is functionally equivalent.

Threat model (per 10-05-PLAN.md ``<threat_model>``):
- T-10-05-01 (Tampering): HMAC-SHA256 signature with ``hmac.compare_digest``.
- T-10-05-02 (Replay): single-use enforced via ``lifecycle_state`` check.
- T-10-05-03 (DoS): not directly mitigated here — token issuance is gated
  by the install endpoint's auth + workspace-role check.

See ``backend/tests/test_app_install_token.py`` for the regression suite.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import secrets
import time
from hashlib import sha256
from typing import Optional

from app.config import settings
from app.exceptions import (
    AppInstallTokenExpiredError,
    AppInstallTokenInvalidError,
)

# Default TTL (hours) for install tokens. Overridable via env var so dev
# tests can lower it (e.g. 0.0001 → ~0.36s for expiry tests) without
# patching the module.
_DEFAULT_TTL_HOURS = 1.0


def _signing_key() -> bytes:
    """Return the HMAC signing key as bytes.

    Reads from ``settings.SECRET_KEY`` (the jvspatial canonical JWT signing
    key, validated >= 32 chars in production by main.py). Encoded as UTF-8
    bytes for ``hmac.new``.
    """
    return settings.SECRET_KEY.encode("utf-8")


def _ttl_seconds(ttl_hours: Optional[float]) -> int:
    """Resolve the TTL in seconds. Precedence: explicit arg > env > default.

    Env var ``APP_INSTALL_TOKEN_TTL_HOURS`` accepts a float (e.g. ``0.5``
    for 30 minutes, ``24`` for a day). Negative or zero values are honored
    so tests can mint immediately-expired tokens.
    """
    if ttl_hours is None:
        env_val = os.environ.get("APP_INSTALL_TOKEN_TTL_HOURS")
        if env_val is not None:
            try:
                ttl_hours = float(env_val)
            except ValueError:
                ttl_hours = _DEFAULT_TTL_HOURS
        else:
            ttl_hours = _DEFAULT_TTL_HOURS
    return int(round(ttl_hours * 3600))


def _b64url_encode(data: bytes) -> str:
    """URL-safe base64 encode without padding (RFC 7515 style)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    """URL-safe base64 decode tolerating missing padding."""
    pad = (-len(s)) % 4
    return base64.urlsafe_b64decode(s + ("=" * pad))


def issue_install_token(
    app_id: str,
    ttl_hours: Optional[float] = None,
) -> str:
    """Mint an HMAC-signed install token for ``app_id``.

    The token is returned as a string in ``<payload>.<signature>`` form,
    where each part is URL-safe base64 (no padding). The payload is a JSON
    object with ``app_id``, ``issued_at`` (unix seconds), ``expires_at``
    (unix seconds), and a 16-byte ``nonce`` (URL-safe base64 — not used
    for cryptographic uniqueness, just to defeat token-equality short-
    circuits if the same App were paused twice within the same second).

    Args:
        app_id: The ``App.id`` the token authorizes. The verify path
            requires this match — a token issued for App A cannot be
            replayed against App B.
        ttl_hours: TTL in hours. ``None`` → env ``APP_INSTALL_TOKEN_TTL_HOURS``
            → ``_DEFAULT_TTL_HOURS``. Zero / negative values are honored so
            tests can verify the expiry branch.

    Returns:
        Opaque token string. Length is bounded (payload JSON + signature
        digest ≈ 180-220 chars depending on app_id length); safe to embed
        in JSON response bodies and URL query strings.
    """
    now = int(time.time())
    ttl = _ttl_seconds(ttl_hours)
    payload = {
        "app_id": app_id,
        "issued_at": now,
        "expires_at": now + ttl,
        "nonce": _b64url_encode(secrets.token_bytes(16)),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    sig = hmac.new(_signing_key(), payload_bytes, sha256).digest()
    return f"{_b64url_encode(payload_bytes)}.{_b64url_encode(sig)}"


def verify_install_token(
    token: str,
    expected_app_id: str,
) -> dict:
    """Verify an install token and return the parsed payload.

    Raises:
        AppInstallTokenInvalidError — token does not parse, signature is
            wrong, or ``expected_app_id`` does not match the payload's
            ``app_id``. Treats all three cases identically (no oracle for
            the attacker — same error_code, same message shape).
        AppInstallTokenExpiredError — token parses + signature-verifies but
            ``expires_at`` is in the past. Distinguished from the invalid
            case so the UI can surface a "session expired" prompt instead
            of "invalid token".

    Returns:
        The parsed payload dict (with ``app_id``, ``issued_at``,
        ``expires_at``, ``nonce``).
    """
    if not isinstance(token, str) or not token:
        raise AppInstallTokenInvalidError(
            message="App install token is empty or not a string",
            details={"expected_app_id": expected_app_id},
        )

    # Split into payload + signature parts.
    parts = token.split(".")
    if len(parts) != 2:
        raise AppInstallTokenInvalidError(
            message="App install token shape invalid (expected payload.signature)",
            details={"expected_app_id": expected_app_id},
        )
    payload_b64, sig_b64 = parts

    # Decode payload bytes + signature bytes.
    try:
        payload_bytes = _b64url_decode(payload_b64)
        sig_bytes = _b64url_decode(sig_b64)
    except Exception:
        raise AppInstallTokenInvalidError(
            message="App install token base64 decode failed",
            details={"expected_app_id": expected_app_id},
        )

    # Constant-time HMAC signature check.
    expected_sig = hmac.new(_signing_key(), payload_bytes, sha256).digest()
    if not hmac.compare_digest(sig_bytes, expected_sig):
        raise AppInstallTokenInvalidError(
            message="App install token signature mismatch",
            details={"expected_app_id": expected_app_id},
        )

    # Payload JSON parse.
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception:
        raise AppInstallTokenInvalidError(
            message="App install token payload JSON malformed",
            details={"expected_app_id": expected_app_id},
        )

    # App ID match — guard against cross-App token replay.
    if str(payload.get("app_id") or "") != expected_app_id:
        raise AppInstallTokenInvalidError(
            message="App install token app_id mismatch",
            details={
                "expected_app_id": expected_app_id,
                "token_app_id": payload.get("app_id"),
            },
        )

    # Expiry check — order matters: signature first (so a tampered
    # expired-token doesn't reveal the expiry path), expiry second.
    expires_at = int(payload.get("expires_at") or 0)
    if expires_at <= int(time.time()):
        raise AppInstallTokenExpiredError(
            message="App install token has expired",
            details={
                "expected_app_id": expected_app_id,
                "expired_at": expires_at,
            },
        )

    return payload
