"""Verified alternate-mailbox password setup for newly provisioned members.

When login credentials must be sent to an inbox different from the account
email, a 6-digit OTP is emailed to the delivery address first. Credentials
are never emailed. A single-use setup link is released after mailbox verification.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

from app.config import settings
from app.models.nodes import User
from app.services.email_service import (
    render_credentials_delivery_confirm_email,
    render_password_reset_email,
    send_email,
)
from app.services.member_provision import (
    _lock_identity,
)

logger = logging.getLogger(__name__)

_PREFS_PENDING = "credentials_delivery"
_PREFS_RELEASE = "credentials_delivery_released"

ERR_INVALID_CODE = "auth.credentials_delivery.code_invalid"
ERR_EXPIRED_CODE = "auth.credentials_delivery.code_expired"
ERR_ATTEMPTS_EXCEEDED = "auth.credentials_delivery.attempts_exceeded"
ERR_NOT_PENDING = "auth.credentials_delivery.not_pending"
ERR_INTENT_MISMATCH = "auth.credentials_delivery.intent_mismatch"


def require_delivery_storage() -> None:
    """Require shared transactional fencing outside single-process development."""
    import os

    from jvspatial.db import get_prime_database

    from app.exceptions import BadRequestError
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
    )

    if not graph_transaction_available(get_prime_database()) and not (
        settings.DEBUG or "PYTEST_CURRENT_TEST" in os.environ
    ):
        raise BadRequestError(
            message="Alternate delivery requires transactional shared storage"
        )


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _expiry_iso() -> str:
    minutes = int(settings.CREDENTIALS_DELIVERY_CODE_EXPIRE_MINUTES)
    return (_now_utc() + timedelta(minutes=minutes)).isoformat()


def _is_expired(expires_at_iso: str) -> bool:
    try:
        ts = datetime.fromisoformat(expires_at_iso)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True
    return _now_utc() >= ts


def _generate_otp() -> Tuple[str, str]:
    code = str(secrets.randbelow(1_000_000)).zfill(6)
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    return code, code_hash


def credentials_verify_page_url(*, member_user_id: str, intent_id: str) -> str:
    """Build the public URL where a member confirms alternate-mailbox OTP delivery."""
    base = settings.APP_BASE_URL.rstrip("/")
    qs = f"member={quote(member_user_id, safe='')}&intent={quote(intent_id, safe='')}"
    return f"{base}/credentials-delivery-verify?{qs}"


async def _save_user_node(user_node: User) -> None:
    """Persist User profile fields on a fresh graph transaction.

    Member provision commits the provisioning graph transaction before alternate
    mailbox OTP is written. Callers may still hold a User instance bound to the
    closed connection — reload and save on the prime store, not the request-scoped
    transaction handle left on ``get_default_context().database``.
    """
    from jvspatial.db import get_prime_database

    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
        postgres_graph_transaction,
    )

    user_id = user_node.id
    preferences = user_node.preferences
    updated_at = user_node.updated_at

    async def _persist(fresh: User) -> None:
        fresh.preferences = preferences
        fresh.updated_at = updated_at
        await fresh.save()

    prime = get_prime_database()
    if graph_transaction_available(prime):
        async with postgres_graph_transaction(prime):
            fresh = await User.get(user_id)
            if fresh is None:
                raise ValueError(f"User {user_id} not found")
            await _persist(fresh)
    else:
        fresh = await User.get(user_id)
        if fresh is None:
            raise ValueError(f"User {user_id} not found")
        await _persist(fresh)

    user_node.preferences = preferences
    user_node.updated_at = updated_at


def delivery_status(user_node: User) -> Dict[str, Any]:
    """Snapshot for admin polling after alternate-mailbox provision."""
    prefs = user_node.preferences or {}
    released = prefs.get(_PREFS_RELEASE) or {}
    if isinstance(released, dict) and released.get("at"):
        return {
            "pending_verification": False,
            "credentials_emailed": True,
            "released_at": released.get("at"),
            "recipient_email": released.get("recipient_email"),
        }
    slot = prefs.get(_PREFS_PENDING) or {}
    if not isinstance(slot, dict) or not slot.get("hash"):
        return {
            "pending_verification": False,
            "credentials_emailed": False,
        }
    expired = _is_expired(str(slot.get("expires_at") or ""))
    return {
        "pending_verification": not expired,
        "credentials_emailed": False,
        "expires_at": slot.get("expires_at"),
        "recipient_email": slot.get("recipient_email"),
        "intent_id": slot.get("intent_id"),
        "expired": expired,
    }


async def begin_alternate_credentials_delivery(
    *,
    user_node: User,
    recipient_email: str,
    account_email: str,
    workspace_id: str,
    workspace_name: str,
    display_name: str,
    welcome_message: Optional[str],
    actor_user_id: str,
) -> Dict[str, Any]:
    """Email a confirmation code; credentials stay unreleased until verify."""
    require_delivery_storage()
    recipient_norm = recipient_email.strip().lower()
    account_norm = account_email.strip().lower()
    if not recipient_norm or recipient_norm == account_norm:
        raise ValueError("alternate delivery requires distinct recipient email")

    from app.services.credential_crypto import encrypt_secret_for_storage

    code, code_hash = _generate_otp()
    intent_id = secrets.token_urlsafe(16)
    verify_url = credentials_verify_page_url(
        member_user_id=user_node.id, intent_id=intent_id
    )

    prefs = dict(user_node.preferences or {})
    prefs.pop(_PREFS_RELEASE, None)
    from app.services.authentication import get_authentication_service

    auth = await get_authentication_service()._get_user_by_id(user_node.user_id)
    if auth is None:
        raise ValueError("Account unavailable")
    prefs[_PREFS_PENDING] = {
        "version": 2,
        "challenge_code_enc": encrypt_secret_for_storage(
            code, aad=f"credentials-challenge:{user_node.id}:{intent_id}"
        ),
        "challenge_attempts": 1,
        "challenge_last_attempt": _now_utc().isoformat(),
        "credential_version": hashlib.sha256(
            str(auth.password_hash).encode()
        ).hexdigest(),
        "intent_id": intent_id,
        "recipient_email": recipient_norm,
        "account_email": account_norm,
        "workspace_id": workspace_id,
        "workspace_name": workspace_name,
        "display_name": display_name,
        "welcome_message": (welcome_message or "").strip() or None,
        "actor_user_id": actor_user_id,
        "hash": code_hash,
        "expires_at": _expiry_iso(),
        "attempts": 0,
    }
    user_node.preferences = prefs
    user_node.updated_at = _now_utc().isoformat()
    await _save_user_node(user_node)

    message = render_credentials_delivery_confirm_email(
        recipient_email=recipient_norm,
        recipient_name=display_name,
        account_email=account_norm,
        code=code,
        verify_url=verify_url,
        expires_minutes=settings.CREDENTIALS_DELIVERY_CODE_EXPIRE_MINUTES,
    )
    message.workspace_id = workspace_id
    message.source_kind = "credentials_delivery_verification"
    message.source_id = user_node.id
    message.actor_user_id = actor_user_id
    try:
        emailed = await send_email(message)
    except Exception:  # noqa: BLE001
        emailed = False

    logger.info(
        "credentials_delivery: challenge sent member_user_id=%s recipient=%s",
        user_node.id,
        recipient_norm,
    )
    return {
        "intent_id": intent_id,
        "verify_url": verify_url,
        "expires_at": prefs[_PREFS_PENDING]["expires_at"],
        "challenge_emailed": emailed,
    }


def _clear_pending(user_node: User) -> None:
    prefs = dict(user_node.preferences or {})
    prefs.pop(_PREFS_PENDING, None)
    user_node.preferences = prefs


def _bump_attempts(user_node: User) -> int:
    prefs = dict(user_node.preferences or {})
    slot = dict(prefs.get(_PREFS_PENDING) or {})
    attempts = int(slot.get("attempts", 0)) + 1
    slot["attempts"] = attempts
    prefs[_PREFS_PENDING] = slot
    user_node.preferences = prefs
    user_node.updated_at = _now_utc().isoformat()
    return attempts


async def _release_credentials_email(
    *,
    user_node: User,
    slot: Dict[str, Any],
    setup_token: str,
) -> bool:
    """Deliver a password-setup link without changing the account password."""
    message = render_password_reset_email(
        recipient_email=str(slot["recipient_email"]),
        recipient_name=str(slot.get("display_name") or ""),
        reset_url=f"{settings.APP_BASE_URL.rstrip('/')}/reset-password?token={quote(setup_token, safe='')}",
        expires_minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES,
    )
    # Authentication mail must use the platform provider, never an App hook.
    message.source_kind = "password_reset"
    return await send_email(message)


async def _fresh_user(user_id: str) -> Optional[User]:
    """Bypass graph identity caches when reading a locked profile row."""
    rows = await User.find({"id": user_id})
    return rows[0] if len(rows) == 1 else None


async def consume_credentials_delivery_code(
    *,
    member_user_id: str,
    intent_id: str,
    code: str,
) -> Tuple[bool, Optional[str], bool]:
    """Claim one verified intent and retry the same durable setup-link delivery.

    The password is never mutated here. The existing single-use password-reset
    flow owns that mutation. A persisted encrypted token survives worker failure;
    a short lease prevents concurrent dispatch. Recovery can resend the SAME
    link after a lease expires, never issue a competing password or token.
    """
    from app.services.authentication import get_authentication_service
    from app.services.credential_crypto import (
        decrypt_secret_from_storage,
        encrypt_secret_for_storage,
    )
    from app.services.entry_write_scope import entry_write_scope
    from app.services.password_reset import _store_token_on_user, generate_reset_token

    if not code or not str(code).strip():
        return False, ERR_INVALID_CODE, False
    require_delivery_storage()
    identity = f"credentials-delivery:{member_user_id}"
    attempt_id = secrets.token_urlsafe(16)
    aad = f"credentials-delivery:{member_user_id}:{intent_id}"
    async with entry_write_scope(identity):
        user = await _fresh_user(member_user_id)
        if user is None:
            return False, ERR_INVALID_CODE, False
        await _lock_identity(user)
        user = await _fresh_user(member_user_id)
        if user is None:
            return False, ERR_NOT_PENDING, False
        slot = dict((user.preferences or {}).get(_PREFS_PENDING) or {})
        if slot.get("version") != 2 or not slot.get("hash"):
            return False, ERR_NOT_PENDING, False
        if slot.get("intent_id") != str(intent_id or "").strip():
            return False, ERR_INTENT_MISMATCH, False
        if _is_expired(str(slot.get("expires_at") or "")):
            _clear_pending(user)
            await user.save()
            return False, ERR_EXPIRED_CODE, False
        digest = hashlib.sha256(str(code).strip().encode()).hexdigest()
        if not secrets.compare_digest(str(slot["hash"]), digest):
            attempts = _bump_attempts(user)
            if attempts >= settings.CREDENTIALS_DELIVERY_MAX_ATTEMPTS:
                _clear_pending(user)
            await user.save()
            return (
                False,
                (
                    ERR_ATTEMPTS_EXCEEDED
                    if attempts >= settings.CREDENTIALS_DELIVERY_MAX_ATTEMPTS
                    else ERR_INVALID_CODE
                ),
                False,
            )
        if slot.get("state") == "sent":
            return True, None, True
        if slot.get("state") == "sending" and not _is_expired(
            str(slot.get("lease_until") or "")
        ):
            return True, None, False
        auth = await get_authentication_service()._get_user_by_id(user.user_id)
        if (
            auth is None
            or not getattr(auth, "is_active", True)
            or str(getattr(auth, "email", "")).strip().lower()
            != slot.get("account_email")
        ):
            return False, ERR_NOT_PENDING, False
        if hashlib.sha256(str(auth.password_hash).encode()).hexdigest() != slot.get(
            "credential_version"
        ):
            return False, ERR_NOT_PENDING, False
        if not slot.get("setup_token_enc"):
            token, token_hash = generate_reset_token()
            encrypted = encrypt_secret_for_storage(token, aad=aad)
            _store_token_on_user(user, token_hash)
            slot.update(setup_token_enc=encrypted, setup_token_hash=token_hash)
            slot.pop("challenge_code_enc", None)
        else:
            if not str(slot["setup_token_enc"]).startswith("v1:"):
                return False, ERR_NOT_PENDING, False
            token = decrypt_secret_from_storage(str(slot["setup_token_enc"]), aad=aad)
            current = (user.preferences or {}).get("reset_token") or {}
            if (
                not token
                or current.get("hash") != slot.get("setup_token_hash")
                or _is_expired(str(current.get("expires_at") or ""))
            ):
                return False, ERR_NOT_PENDING, False
        slot.update(
            state="sending",
            attempt_id=attempt_id,
            lease_until=(_now_utc() + timedelta(seconds=60)).isoformat(),
        )
        user.preferences = {**(user.preferences or {}), _PREFS_PENDING: slot}
        await user.save()

    # Outbound I/O is outside the graph transaction. An uncertain send may be
    # repeated after lease expiry; the reset token remains identical and single-use.
    try:
        emailed = await _release_credentials_email(
            user_node=user, slot=slot, setup_token=token
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "credentials_delivery: provider failed member=%s", member_user_id
        )
        emailed = False
    async with entry_write_scope(identity):
        fresh = await _fresh_user(member_user_id)
        if fresh is None:
            return False, ERR_NOT_PENDING, False
        await _lock_identity(fresh)
        fresh = await _fresh_user(member_user_id)
        if fresh is None:
            return False, ERR_NOT_PENDING, False
        current = dict((fresh.preferences or {}).get(_PREFS_PENDING) or {})
        if (
            current.get("intent_id") != intent_id
            or current.get("attempt_id") != attempt_id
        ):
            return True, None, False
        current["state"] = "sent" if emailed else "ready"
        if emailed:
            current.pop("setup_token_enc", None)
            fresh.preferences = {
                **(fresh.preferences or {}),
                _PREFS_RELEASE: {
                    "at": _now_utc().isoformat(),
                    "recipient_email": slot["recipient_email"],
                },
            }
        fresh.preferences = {**(fresh.preferences or {}), _PREFS_PENDING: current}
        fresh.updated_at = _now_utc().isoformat()
        await fresh.save()
    return True, None, emailed


async def resend_credentials_delivery_code(
    *, member_user_id: str, intent_id: str
) -> bool:
    """Retry a challenge to its fixed mailbox without resetting code or attempts.

    The unguessable intent is required. Each intent permits at most three
    challenge sends, at least thirty seconds apart. Retries cannot change the
    recipient, extend expiry or reset the verification attempt counter.
    """
    from app.services.credential_crypto import decrypt_secret_from_storage
    from app.services.entry_write_scope import entry_write_scope

    require_delivery_storage()
    async with entry_write_scope(f"credentials-delivery:{member_user_id}"):
        user = await _fresh_user(member_user_id)
        if user is None:
            return False
        await _lock_identity(user)
        user = await _fresh_user(member_user_id)
        if user is None:
            return False
        slot = dict((user.preferences or {}).get(_PREFS_PENDING) or {})
        if (
            slot.get("version") != 2
            or slot.get("intent_id") != intent_id
            or _is_expired(str(slot.get("expires_at") or ""))
        ):
            return False
        if slot.get("state") or int(slot.get("challenge_attempts", 0)) >= 3:
            return False
        try:
            last = datetime.fromisoformat(slot["challenge_last_attempt"])
            if (_now_utc() - last).total_seconds() < 30:
                return False
        except (KeyError, TypeError, ValueError):
            return False
        encrypted = str(slot.get("challenge_code_enc") or "")
        if not encrypted.startswith("v1:"):
            return False
        code = decrypt_secret_from_storage(
            encrypted, aad=f"credentials-challenge:{member_user_id}:{intent_id}"
        )
        if not code:
            return False
        slot.update(
            challenge_attempts=int(slot["challenge_attempts"]) + 1,
            challenge_last_attempt=_now_utc().isoformat(),
        )
        user.preferences = {**(user.preferences or {}), _PREFS_PENDING: slot}
        await user.save()
    message = render_credentials_delivery_confirm_email(
        recipient_email=slot["recipient_email"],
        recipient_name=slot.get("display_name"),
        account_email=slot["account_email"],
        code=code,
        verify_url=credentials_verify_page_url(
            member_user_id=member_user_id, intent_id=intent_id
        ),
        expires_minutes=settings.CREDENTIALS_DELIVERY_CODE_EXPIRE_MINUTES,
    )
    message.source_kind = "credentials_delivery_verification"
    try:
        return await send_email(message)
    except Exception:  # noqa: BLE001
        logger.warning(
            "credentials_delivery: challenge retry failed member=%s", member_user_id
        )
        return False
