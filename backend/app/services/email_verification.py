"""Email verification flow.

Single-use, time-limited 6-digit OTP stored as the SHA-256 hash of the
plaintext code on ``User.preferences["email_verification"]``. Same storage
pattern as ``password_reset.py``: only the hash lives in the database; the
plaintext OTP is sent in the email and never persisted.

Non-blocking by design: login is not gated on ``email_verified``. The frontend
shows a persistent banner until the user completes verification.

Public surface:
    ``await create_verification_request(user_node, email)``
        — generates OTP, stores hash, sends email. Always returns silently;
          email failures are logged, not raised.

    ``await consume_verification_code(user_node, code)``
        — validates the OTP, sets ``email_verified = True``, clears the slot.
          Returns ``(ok: bool, error_code: Optional[str])``.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from app.config import settings
from app.models.nodes import User
from app.services.email_service import render_verification_email, send_email

logger = logging.getLogger(__name__)

ERR_INVALID_CODE = "auth.verify.code_invalid"
ERR_EXPIRED_CODE = "auth.verify.code_expired"
ERR_ATTEMPTS_EXCEEDED = "auth.verify.attempts_exceeded"
ERR_ALREADY_VERIFIED = "auth.verify.already_verified"

_PREFS_KEY = "email_verification"


# ─────────────────────────────────────────────────────────────────────────────
# OTP primitives
# ─────────────────────────────────────────────────────────────────────────────


def _generate_otp() -> Tuple[str, str]:
    """Return ``(plaintext_6_digit_code, sha256_hash)``."""
    code = str(secrets.randbelow(1_000_000)).zfill(6)
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    return code, code_hash


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _expiry_iso() -> str:
    minutes = settings.EMAIL_VERIFICATION_CODE_EXPIRE_MINUTES
    return (_now_utc() + timedelta(minutes=minutes)).isoformat()


def _is_expired(expires_at_iso: str) -> bool:
    try:
        ts = datetime.fromisoformat(expires_at_iso)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True
    return _now_utc() >= ts


# ─────────────────────────────────────────────────────────────────────────────
# Storage helpers — email_verification sub-object on User.preferences
# ─────────────────────────────────────────────────────────────────────────────


def _store_otp(user_node: User, code_hash: str) -> None:
    prefs = dict(user_node.preferences or {})
    prefs[_PREFS_KEY] = {
        "hash": code_hash,
        "expires_at": _expiry_iso(),
        "attempts": 0,
    }
    user_node.preferences = prefs
    user_node.updated_at = _now_utc().isoformat()


def _clear_otp(user_node: User) -> None:
    prefs = dict(user_node.preferences or {})
    if _PREFS_KEY in prefs:
        prefs.pop(_PREFS_KEY, None)
        user_node.preferences = prefs
        user_node.updated_at = _now_utc().isoformat()


def _bump_attempts(user_node: User) -> int:
    prefs = dict(user_node.preferences or {})
    slot = dict(prefs.get(_PREFS_KEY) or {})
    attempts = int(slot.get("attempts", 0)) + 1
    slot["attempts"] = attempts
    prefs[_PREFS_KEY] = slot
    user_node.preferences = prefs
    user_node.updated_at = _now_utc().isoformat()
    return attempts


# ─────────────────────────────────────────────────────────────────────────────
# Public surface
# ─────────────────────────────────────────────────────────────────────────────


def has_pending_code(user_node: User) -> bool:
    """Return True if the user has a non-expired verification OTP on file."""
    slot = (user_node.preferences or {}).get(_PREFS_KEY) or {}
    stored_hash = slot.get("hash") or ""
    expires_at = slot.get("expires_at") or ""
    return bool(stored_hash) and not _is_expired(expires_at)


async def create_verification_request(user_node: User, email: str) -> None:
    """Generate a verification OTP for ``user_node`` and email it.

    Overwrites any prior pending OTP (one outstanding code per account).
    Email failures are logged but not raised — a flaky provider must not
    break the signup flow.
    """
    code, code_hash = _generate_otp()
    _store_otp(user_node, code_hash)
    await user_node.save()

    name = user_node.display_name or None
    message = render_verification_email(
        recipient_email=email,
        recipient_name=name,
        code=code,
        expires_minutes=settings.EMAIL_VERIFICATION_CODE_EXPIRE_MINUTES,
    )
    with contextlib.suppress(Exception):
        await send_email(message)

    logger.info(
        "email_verification: OTP generated for user_id=%s (email=%s)",
        user_node.id,
        email,
    )


async def consume_verification_code(
    user_node: User, code: str
) -> Tuple[bool, Optional[str]]:
    """Validate the OTP and mark ``user_node.email_verified = True`` on success.

    Returns ``(ok, error_code)``. ``error_code`` is one of the ERR_* constants
    on failure, or ``None`` on success.
    """
    if user_node.email_verified:
        return False, ERR_ALREADY_VERIFIED

    if not code or not code.strip():
        return False, ERR_INVALID_CODE

    slot = (user_node.preferences or {}).get(_PREFS_KEY) or {}
    stored_hash = slot.get("hash") or ""
    expires_at = slot.get("expires_at") or ""

    if not stored_hash:
        return False, ERR_INVALID_CODE

    # Check expiry before hash comparison so an expired code is rejected
    # regardless of whether the submitted digit sequence happens to be correct.
    if _is_expired(expires_at):
        _clear_otp(user_node)
        await user_node.save()
        return False, ERR_EXPIRED_CODE

    code_hash = hashlib.sha256(code.strip().encode("utf-8")).hexdigest()

    if not secrets.compare_digest(stored_hash, code_hash):
        new_attempts = _bump_attempts(user_node)
        await user_node.save()
        if new_attempts >= settings.EMAIL_VERIFICATION_MAX_ATTEMPTS:
            _clear_otp(user_node)
            await user_node.save()
            return False, ERR_ATTEMPTS_EXCEEDED
        return False, ERR_INVALID_CODE

    user_node.email_verified = True
    _clear_otp(user_node)
    await user_node.save()

    logger.info(
        "email_verification: verified user_id=%s",
        user_node.id,
    )
    return True, None
