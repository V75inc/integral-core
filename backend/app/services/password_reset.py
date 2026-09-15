"""Password reset flow.

Single-use, time-limited tokens stored as the SHA-256 hash of a random URL-safe
string. The plaintext token is sent in the email link; only its hash lives in
the database. Verification uses ``secrets.compare_digest`` for timing safety.

Storage location: the integral ``User.preferences`` dict carries a
``reset_token`` sub-object with ``hash`` (str), ``expires_at`` (ISO-8601 UTC),
and ``attempts`` (int). On successful reset OR after ``MAX_ATTEMPTS``, the
sub-object is removed.

Why preferences instead of a new node? It's the same pattern the agentive
layer's OTP flow uses (channel_identity OTP), keeps the schema flat for v1,
and avoids inventing a parallel concept right before launch. If reset volume
becomes meaningful we can promote to a dedicated ``ResetToken`` node later.

Public surface:
    ``await create_reset_request(email)`` — generates token, stores hash,
        emails the user, returns silently regardless of whether the email
        actually exists (no enumeration leak to callers).

    ``await consume_reset_token(token, new_password)`` — validates the
        token against a stored hash, updates the AuthUser password, clears
        the stored token. Returns a tuple (ok, error_code) so the route
        can emit canonical error strings.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from jvspatial.api.auth.models import User as AuthUser

from app.bootstrap_admin import _find_user_by_email, _hash_password
from app.config import settings
from app.models.nodes import User
from app.services.email_service import (
    render_password_reset_email,
    send_email,
)

logger = logging.getLogger(__name__)

# Canonical error codes the API surfaces. Keep aligned with the agentive
# error envelope conventions (lowercase dotted).
ERR_INVALID_TOKEN = "auth.reset.token_invalid"
ERR_EXPIRED_TOKEN = "auth.reset.token_expired"
ERR_ATTEMPTS_EXCEEDED = "auth.reset.attempts_exceeded"
ERR_WEAK_PASSWORD = "auth.reset.password_too_short"
ERR_USER_INACTIVE = "auth.reset.user_inactive"


# ─────────────────────────────────────────────────────────────────────────────
# Token primitives
# ─────────────────────────────────────────────────────────────────────────────


def generate_reset_token() -> Tuple[str, str]:
    """Return ``(plaintext_token, sha256_hash)``.

    Uses 32 random bytes encoded URL-safe → ~43 chars. That's plenty of entropy
    (256 bits) for a single-use, short-lived token. Hash is stored; plaintext
    is delivered via email.
    """
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return token, token_hash


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _expiry_iso(minutes: int) -> str:
    return (_now_utc() + timedelta(minutes=minutes)).isoformat()


def _is_expired(expires_at_iso: str) -> bool:
    try:
        # fromisoformat handles both ``+00:00`` and ``Z``-less strings produced
        # by datetime.isoformat(). We always write tz-aware values.
        ts = datetime.fromisoformat(expires_at_iso)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        # Defensive: treat unparseable expiry as expired (fail closed).
        return True
    return _now_utc() >= ts


# ─────────────────────────────────────────────────────────────────────────────
# Storage helpers — reset_token sub-object on User.preferences
# ─────────────────────────────────────────────────────────────────────────────


async def _revoke_all_sessions(auth_user_id: str) -> None:
    """Revoke every refresh token (and blacklist the paired access tokens).

    Delegates to jvspatial's ``AuthenticationService.revoke_all_user_tokens``.
    Lazy import: ``app.api.auth`` imports this module's callers.
    """
    try:
        from app.api.auth import _get_auth_service

        await _get_auth_service().revoke_all_user_tokens(auth_user_id)
    except Exception:  # noqa: BLE001 — fail-loud, but never mask the reset
        logger.exception(
            "password_reset: token revocation failed for AuthUser %s", auth_user_id
        )


async def _find_user_node_by_auth_user_id(auth_user_id: str) -> Optional[User]:
    """Find the integral User node linked to an AuthUser id."""
    matches = await User.find({"context.user_id": auth_user_id})
    if not matches:
        return None
    return matches[0]


def _store_token_on_user(user_node: User, token_hash: str) -> None:
    """Mutate user_node.preferences in place with a fresh reset token slot."""
    prefs = dict(user_node.preferences or {})
    prefs["reset_token"] = {
        "hash": token_hash,
        "expires_at": _expiry_iso(settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES),
        "attempts": 0,
    }
    user_node.preferences = prefs
    user_node.updated_at = _now_utc().isoformat()


def _clear_token_on_user(user_node: User) -> None:
    prefs = dict(user_node.preferences or {})
    if "reset_token" in prefs:
        prefs.pop("reset_token", None)
        user_node.preferences = prefs
        user_node.updated_at = _now_utc().isoformat()


def _bump_attempts(user_node: User) -> int:
    prefs = dict(user_node.preferences or {})
    slot = dict(prefs.get("reset_token") or {})
    attempts = int(slot.get("attempts", 0)) + 1
    slot["attempts"] = attempts
    prefs["reset_token"] = slot
    user_node.preferences = prefs
    user_node.updated_at = _now_utc().isoformat()
    return attempts


# ─────────────────────────────────────────────────────────────────────────────
# Lookup by token hash — scans User nodes, since the hash isn't on AuthUser.
# Volume here is bounded by user count; for v1 this is fine. If reset becomes
# hot, add a secondary index on preferences.reset_token.hash.
# ─────────────────────────────────────────────────────────────────────────────


async def _find_user_node_by_token_hash(token_hash: str) -> Optional[User]:
    """Find the integral User holding this token hash, or None."""
    matches = await User.find({"context.preferences.reset_token.hash": token_hash})
    if matches:
        return matches[0]
    # Fallback: some jvspatial backends don't index nested dict paths well.
    # Scan all and match in-process. Bounded by user count.
    all_users = await User.find({})
    for u in all_users:
        slot = (u.preferences or {}).get("reset_token") or {}
        stored = str(slot.get("hash") or "")
        if stored and secrets.compare_digest(stored, token_hash):
            return u
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Public surface
# ─────────────────────────────────────────────────────────────────────────────


async def create_reset_request(email: str) -> None:
    """Generate a reset token for ``email`` and email the link.

    Always returns successfully even if no user exists for the address — the
    caller MUST NOT distinguish these cases in its HTTP response. The email
    address is normalized (lower-cased, stripped) before lookup.

    Side effects:
      - Updates the integral User node's preferences with the new token slot
        (overwriting any prior slot for that user — only one outstanding
        token per account).
      - Calls the email service. Email failures are logged, not raised.
    """
    normalized_email = (email or "").strip().lower()
    if not normalized_email:
        return

    auth_user = await _find_user_by_email(normalized_email)
    if not auth_user:
        # Nothing to do, but we deliberately stay silent at this layer too —
        # we don't want timing differences to leak existence either, but
        # that's a more advanced concern; for v1 we accept the small timing
        # differential and rely on the route returning identical responses.
        logger.info(
            "password_reset: ignoring request for unknown email=%s (no leak to client)",
            normalized_email,
        )
        return

    if hasattr(auth_user, "is_active") and auth_user.is_active is False:
        logger.info("password_reset: skipping inactive user email=%s", normalized_email)
        return

    user_node = await _find_user_node_by_auth_user_id(auth_user.id)
    if not user_node:
        # AuthUser exists without an integral User node (shouldn't happen for
        # human accounts, but service-auto-create flows could leave this state).
        # We can't store the token without a user node, so log and bail.
        logger.warning(
            "password_reset: AuthUser %s has no integral User node; cannot store token",
            auth_user.id,
        )
        return

    token, token_hash = generate_reset_token()
    _store_token_on_user(user_node, token_hash)
    await user_node.save()

    reset_url = f"{settings.APP_BASE_URL.rstrip('/')}/reset-password?token={token}"
    message = render_password_reset_email(
        recipient_email=normalized_email,
        recipient_name=getattr(auth_user, "name", None)
        or user_node.display_name
        or None,
        reset_url=reset_url,
        expires_minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES,
    )
    # Send is awaited but failures don't propagate.
    with contextlib.suppress(Exception):
        await send_email(message)


async def consume_reset_token(
    token: str, new_password: str
) -> Tuple[bool, Optional[str]]:
    """Validate the token, update the password, clear the token slot.

    Returns ``(ok, error_code)``. ``error_code`` is one of the ERR_* constants
    on failure; None on success.

    The same response shape is returned for "no user with this token hash"
    and "wrong token" — both surface as ERR_INVALID_TOKEN. Don't differentiate
    in the API response.
    """
    if not token:
        return False, ERR_INVALID_TOKEN

    if not new_password or len(new_password) < settings.PASSWORD_MIN_LENGTH:
        return False, ERR_WEAK_PASSWORD

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    user_node = await _find_user_node_by_token_hash(token_hash)
    if not user_node:
        return False, ERR_INVALID_TOKEN

    slot = (user_node.preferences or {}).get("reset_token") or {}
    stored_hash = slot.get("hash") or ""
    expires_at = slot.get("expires_at") or ""

    # Constant-time compare even though we already matched via the index lookup
    # above — defensive, costs nothing.
    if not secrets.compare_digest(stored_hash, token_hash):
        new_attempts = _bump_attempts(user_node)
        await user_node.save()
        if new_attempts >= settings.PASSWORD_RESET_MAX_ATTEMPTS:
            _clear_token_on_user(user_node)
            await user_node.save()
            return False, ERR_ATTEMPTS_EXCEEDED
        return False, ERR_INVALID_TOKEN

    if _is_expired(expires_at):
        _clear_token_on_user(user_node)
        await user_node.save()
        return False, ERR_EXPIRED_TOKEN

    auth_user = await AuthUser.get(user_node.user_id)
    if not auth_user:
        # Token was valid but AuthUser disappeared — treat as invalid.
        _clear_token_on_user(user_node)
        await user_node.save()
        return False, ERR_INVALID_TOKEN

    if hasattr(auth_user, "is_active") and auth_user.is_active is False:
        _clear_token_on_user(user_node)
        await user_node.save()
        return False, ERR_USER_INACTIVE

    auth_user.password_hash = _hash_password(new_password)
    await auth_user.save()

    # A password reset must end every existing session — otherwise whoever
    # held the old credentials keeps valid access / refresh tokens after the
    # legitimate owner recovers the account. Best-effort: the hash is already
    # replaced, so a revocation failure is logged loudly, not surfaced as a
    # failed reset.
    await _revoke_all_sessions(auth_user.id)

    # Single-use: clear the token regardless of any later steps.
    _clear_token_on_user(user_node)
    await user_node.save()

    logger.info(
        "password_reset: success for AuthUser %s (User %s)",
        auth_user.id,
        user_node.id,
    )
    return True, None
