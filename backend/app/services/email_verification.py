"""Email verification flow.

Single-use, time-limited 6-digit OTP stored as the SHA-256 hash of the
plaintext code on ``User.preferences["email_verification"]``. Same storage
pattern as ``password_reset.py``: only the hash lives in the database; the
plaintext OTP is sent in the email and never persisted.

Non-blocking by design: login is not gated on ``email_verified``. The frontend
shows a persistent banner until the user completes verification.

Public surface:
    ``await create_verification_request(user_node, email)``
        — generates OTP, stores hash, sends email. Returns delivery success;
          signup remains non-blocking on delivery failure.

    ``await consume_verification_code(user_node, code)``
        — validates the OTP, sets ``email_verified = True``, clears the slot.
          Returns ``(ok: bool, error_code: Optional[str])``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from weakref import WeakValueDictionary

from jvspatial.db.database import Database

from app.config import settings
from app.models.nodes import User
from app.services.email_identity import current_account_email, verified_current_email
from app.services.email_service import render_verification_email, send_email

logger = logging.getLogger(__name__)

ERR_INVALID_CODE = "auth.verify.code_invalid"
ERR_EXPIRED_CODE = "auth.verify.code_expired"
ERR_ATTEMPTS_EXCEEDED = "auth.verify.attempts_exceeded"
ERR_ALREADY_VERIFIED = "auth.verify.already_verified"

_PREFS_KEY = "email_verification"
_RESEND_INTERVAL_SECONDS = 60
_RESEND_LOCKS: WeakValueDictionary[tuple[int, int, str], asyncio.Lock] = (
    WeakValueDictionary()
)


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


def _store_otp(user_node: User, code_hash: str, email: str) -> None:
    prefs = dict(user_node.preferences or {})
    prefs[_PREFS_KEY] = {
        "hash": code_hash,
        "expires_at": _expiry_iso(),
        "attempts": 0,
        "email": email.strip().casefold(),
        "requested_at": _now_utc().isoformat(),
    }
    user_node.preferences = prefs
    user_node.updated_at = _now_utc().isoformat()


def _verification_lock(db, user_id: str):
    """Native CAS in hosted stores; one local lock for development JSON stores."""
    from app.api.errors import ServiceUnavailableError

    concrete = db
    while getattr(concrete, "inner", None) is not None:
        concrete = concrete.inner
    if type(concrete).find_one_and_update is not Database.find_one_and_update:
        return None
    if type(concrete).__module__ not in {"jvspatial.db.jsondb", "jvspatial.memory"}:
        raise ServiceUnavailableError(
            message="Email verification is temporarily unavailable."
        )
    return _RESEND_LOCKS.setdefault(
        (id(concrete), id(asyncio.get_running_loop()), str(user_id)), asyncio.Lock()
    )


def _slot_match(user_id, slot):
    # Scalar predicates stay on PostgreSQL's native locking/CAS path.
    path = "context.preferences.email_verification"
    return {
        "id": user_id,
        "entity": User._entity_name(),
        **{
            f"{path}.{key}": slot.get(key)
            for key in ("hash", "email", "expires_at", "requested_at", "attempts")
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public surface
# ─────────────────────────────────────────────────────────────────────────────


def has_pending_code(user_node: User, email: Optional[str] = None) -> bool:
    """Return True if the user has a non-expired verification OTP on file."""
    slot = (user_node.preferences or {}).get(_PREFS_KEY) or {}
    stored_hash = slot.get("hash") or ""
    expires_at = slot.get("expires_at") or ""
    return (
        bool(stored_hash)
        and not _is_expired(expires_at)
        and (email is None or slot.get("email") == email.strip().casefold())
    )


async def create_verification_request(user_node: User, email: str) -> bool:
    """Generate a verification OTP for ``user_node`` and email it.

    Overwrites any prior pending OTP (one outstanding code per account).
    Email failures return False rather than breaking signup. Authenticated
    resend uses its own atomic cooldown and reports failed delivery.
    """
    code, code_hash = _generate_otp()
    _store_otp(user_node, code_hash, email)
    await user_node.save()

    name = user_node.display_name or None
    message = render_verification_email(
        recipient_email=email,
        recipient_name=name,
        code=code,
        expires_minutes=settings.EMAIL_VERIFICATION_CODE_EXPIRE_MINUTES,
    )
    try:
        sent = bool(await send_email(message))
    except Exception:
        logger.exception("email_verification: delivery failed user_id=%s", user_node.id)
        sent = False

    logger.info(
        "email_verification: OTP generated for user_id=%s (email=%s)",
        user_node.id,
        email,
    )
    return sent


async def resend_verification_request(user_node: User, email: str) -> None:
    """Actually resend after a short cooldown, rather than waiting for OTP expiry.

    Claim only the sensitive OTP slot with database CAS so concurrent requests
    cannot all dispatch email or overwrite unrelated account preferences. JSON
    is a single-process development store; serialize its non-native CAS locally.
    A rejected delivery retains any previously valid code and reports failure.
    """
    from app.api.errors import RateLimitedError, ServiceUnavailableError

    ctx = await user_node.get_context()
    db = ctx.database
    lock = _verification_lock(db, user_node.id)
    collection = ctx._get_collection_name("n")
    path = "context.preferences.email_verification"

    def slot_match(slot):
        return _slot_match(user_node.id, slot)

    async def claim():
        current = await db.find_one(collection, {"id": user_node.id})
        if current is None:
            raise ServiceUnavailableError(
                message="Could not read email verification status."
            )
        slot = dict(
            (current.get("context", {}).get("preferences") or {}).get(_PREFS_KEY) or {}
        )
        same_email = slot.get("email") == email.strip().casefold()
        try:
            requested = datetime.fromisoformat(slot.get("requested_at", ""))
            requested = (
                requested.replace(tzinfo=timezone.utc)
                if requested.tzinfo is None
                else requested
            )
            remaining = (
                _RESEND_INTERVAL_SECONDS - (_now_utc() - requested).total_seconds()
            )
        except (TypeError, ValueError):
            remaining = 0
        if same_email and remaining > 0:
            raise RateLimitedError(
                message=f"Please wait {max(1, int(remaining) + 1)} seconds before requesting another code.",
                details={"retry_after_seconds": max(1, int(remaining) + 1)},
            )
        code, code_hash = _generate_otp()
        new_slot = {
            "hash": code_hash,
            "email": email.strip().casefold(),
            "expires_at": _expiry_iso(),
            "requested_at": _now_utc().isoformat(),
            "attempts": (
                int(slot.get("attempts", 0))
                if same_email and not _is_expired(slot.get("expires_at", ""))
                else 0
            ),
        }
        saved = await db.find_one_and_update(
            collection,
            slot_match(slot),
            {"$set": {path: new_slot}},
        )
        if saved is None:
            raise RateLimitedError(
                message="Another verification request is in progress. Please try again shortly."
            )
        await ctx._evict_from_cache(user_node.id)
        return code, slot, new_slot

    if lock is None:
        code, previous, claimed = await claim()
    else:
        async with lock:
            code, previous, claimed = await claim()
    message = render_verification_email(
        recipient_email=email,
        recipient_name=user_node.display_name or None,
        code=code,
        expires_minutes=settings.EMAIL_VERIFICATION_CODE_EXPIRE_MINUTES,
    )
    try:
        sent = bool(await send_email(message))
    except Exception:
        logger.exception("email_verification: resend failed user_id=%s", user_node.id)
        sent = False
    if not sent:
        # Only restore if verification/another writer has not changed this slot.
        restored = {
            **previous,
            "requested_at": claimed["requested_at"],
            "email": claimed["email"],
        }

        async def restore():
            await db.find_one_and_update(
                collection,
                slot_match(claimed),
                {"$set": {path: restored}},
            )

        if lock is None:
            await restore()
        else:
            async with lock:
                await restore()
        await ctx._evict_from_cache(user_node.id)
        raise ServiceUnavailableError(
            message="We couldn’t send your verification code. Please try again in a minute."
        )


async def consume_verification_code(
    user_node: User, code: str
) -> Tuple[bool, Optional[str]]:
    """Validate the OTP and mark ``user_node.email_verified = True`` on success.

    Returns ``(ok, error_code)``. ``error_code`` is one of the ERR_* constants
    on failure, or ``None`` on success.
    """
    from app.api.errors import ServiceUnavailableError

    current_email = await current_account_email(user_node)
    if current_email and await verified_current_email(user_node) == current_email:
        return False, ERR_ALREADY_VERIFIED

    if not code or not code.strip():
        return False, ERR_INVALID_CODE

    ctx = await user_node.get_context()
    db = ctx.database
    collection = ctx._get_collection_name("n")
    path = "context.preferences.email_verification"
    lock = _verification_lock(db, user_node.id)

    async def consume():
        # Never save a stale whole User after inspecting its old OTP. Resend,
        # failed-code attempts and successful consumption share the same slot
        # CAS, so none can overwrite a newer code or unrelated preferences.
        for _attempt in range(3):
            row = await db.find_one(collection, {"id": user_node.id})
            if row is None:
                return False, ERR_INVALID_CODE
            current = row.get("context") or {}
            if (
                current.get("email_verified")
                and current.get("verified_email") == current_email
            ):
                return False, ERR_ALREADY_VERIFIED
            slot = dict((current.get("preferences") or {}).get(_PREFS_KEY) or {})
            stored_hash = slot.get("hash") or ""
            if not stored_hash:
                return False, ERR_INVALID_CODE
            updates = {"$set": {"context.updated_at": _now_utc().isoformat()}}
            if _is_expired(slot.get("expires_at", "")):
                result = (False, ERR_EXPIRED_CODE)
                updates["$unset"] = {path: ""}
            elif not current_email or slot.get("email") != current_email:
                result = (False, ERR_INVALID_CODE)
                updates["$unset"] = {path: ""}
            elif not secrets.compare_digest(
                stored_hash, hashlib.sha256(code.strip().encode("utf-8")).hexdigest()
            ):
                attempts = int(slot.get("attempts", 0)) + 1
                if attempts >= settings.EMAIL_VERIFICATION_MAX_ATTEMPTS:
                    result = (False, ERR_ATTEMPTS_EXCEEDED)
                    updates["$unset"] = {path: ""}
                else:
                    result = (False, ERR_INVALID_CODE)
                    updates["$set"][f"{path}.attempts"] = attempts
            else:
                result = (True, None)
                updates["$set"].update(
                    {
                        "context.email_verified": True,
                        "context.verified_email": current_email,
                    }
                )
                updates["$unset"] = {path: ""}
            saved = await db.find_one_and_update(
                collection, _slot_match(user_node.id, slot), updates
            )
            if saved is None:
                continue
            updated = saved.get("context") or {}
            user_node.preferences = dict(updated.get("preferences") or {})
            user_node.email_verified = bool(updated.get("email_verified"))
            user_node.verified_email = updated.get("verified_email") or ""
            user_node.updated_at = updated.get("updated_at") or user_node.updated_at
            await ctx._evict_from_cache(user_node.id)
            return result
        raise ServiceUnavailableError(
            message="Your verification status changed. Please try the current code again."
        )

    if lock is None:
        result = await consume()
    else:
        async with lock:
            result = await consume()
    if result[0]:
        logger.info("email_verification: verified user_id=%s", user_node.id)
    return result
