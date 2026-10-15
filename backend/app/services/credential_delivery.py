"""Verified alternate-mailbox credential delivery (hire / member provision).

When login credentials must be sent to an inbox different from the account
email, a 6-digit OTP is emailed to the delivery address first. Credentials
are released only after the recipient confirms control of that mailbox.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

from app.bootstrap_admin import _hash_password
from app.config import settings
from app.models.nodes import User
from app.services.email_service import (
    render_credentials_delivery_confirm_email,
    render_member_provision_email,
    send_email,
)
from app.services.member_provision import (
    generate_temp_password,
    set_must_change_password,
)

logger = logging.getLogger(__name__)

_PREFS_PENDING = "credentials_delivery"
_PREFS_RELEASE = "credentials_delivery_released"

ERR_INVALID_CODE = "auth.credentials_delivery.code_invalid"
ERR_EXPIRED_CODE = "auth.credentials_delivery.code_expired"
ERR_ATTEMPTS_EXCEEDED = "auth.credentials_delivery.attempts_exceeded"
ERR_NOT_PENDING = "auth.credentials_delivery.not_pending"
ERR_INTENT_MISMATCH = "auth.credentials_delivery.intent_mismatch"


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
    return f"{base}/credentials-delivery-verify.html?{qs}"


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
    recipient_norm = recipient_email.strip().lower()
    account_norm = account_email.strip().lower()
    if not recipient_norm or recipient_norm == account_norm:
        raise ValueError("alternate delivery requires distinct recipient email")

    code, code_hash = _generate_otp()
    intent_id = secrets.token_urlsafe(16)
    verify_url = credentials_verify_page_url(
        member_user_id=user_node.id, intent_id=intent_id
    )

    prefs = dict(user_node.preferences or {})
    prefs.pop(_PREFS_RELEASE, None)
    prefs[_PREFS_PENDING] = {
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
    emailed = await send_email(message)

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


def _mark_released(user_node: User, *, recipient_email: str) -> None:
    prefs = dict(user_node.preferences or {})
    prefs.pop(_PREFS_PENDING, None)
    prefs[_PREFS_RELEASE] = {
        "at": _now_utc().isoformat(),
        "recipient_email": recipient_email.strip().lower(),
    }
    user_node.preferences = prefs
    user_node.updated_at = _now_utc().isoformat()


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
    temp_password: str,
) -> bool:
    from app.config import settings as app_settings

    login_url = f"{app_settings.APP_BASE_URL.rstrip('/')}/login"
    recipient = str(slot.get("recipient_email") or "")
    account = str(slot.get("account_email") or "")
    ws_name = str(slot.get("workspace_name") or "your organization")
    display_name = str(slot.get("display_name") or "")
    welcome = slot.get("welcome_message")

    message = render_member_provision_email(
        recipient_email=recipient,
        recipient_name=display_name,
        workspace_name=ws_name,
        login_url=login_url,
        account_email=account,
        temp_password=temp_password,
        welcome_message=welcome,
    )
    message.workspace_id = str(slot.get("workspace_id") or "")
    message.source_kind = "member_provision"
    message.source_id = user_node.id
    message.actor_user_id = str(slot.get("actor_user_id") or "")
    return await send_email(message)


async def consume_credentials_delivery_code(
    *,
    member_user_id: str,
    intent_id: str,
    code: str,
) -> Tuple[bool, Optional[str], bool]:
    """Validate OTP and email login credentials. Returns (ok, error_code, emailed)."""
    user_node = await User.get(member_user_id)
    if user_node is None:
        return False, ERR_INVALID_CODE, False

    if not code or not str(code).strip():
        return False, ERR_INVALID_CODE, False

    slot = (user_node.preferences or {}).get(_PREFS_PENDING) or {}
    if not isinstance(slot, dict) or not slot.get("hash"):
        return False, ERR_NOT_PENDING, False

    if str(slot.get("intent_id") or "") != str(intent_id or "").strip():
        return False, ERR_INTENT_MISMATCH, False

    expires_at = str(slot.get("expires_at") or "")
    if _is_expired(expires_at):
        _clear_pending(user_node)
        await user_node.save()
        return False, ERR_EXPIRED_CODE, False

    code_hash = hashlib.sha256(str(code).strip().encode("utf-8")).hexdigest()
    stored_hash = str(slot.get("hash") or "")
    if not secrets.compare_digest(stored_hash, code_hash):
        attempts = _bump_attempts(user_node)
        await user_node.save()
        if attempts >= settings.CREDENTIALS_DELIVERY_MAX_ATTEMPTS:
            _clear_pending(user_node)
            await user_node.save()
            return False, ERR_ATTEMPTS_EXCEEDED, False
        return False, ERR_INVALID_CODE, False

    if not user_node.user_id:
        _clear_pending(user_node)
        await user_node.save()
        return False, ERR_NOT_PENDING, False

    from app.services.authentication import get_authentication_service

    auth_user = await get_authentication_service()._get_user_by_id(user_node.user_id)
    if auth_user is None or getattr(auth_user, "is_active", True) is False:
        _clear_pending(user_node)
        await user_node.save()
        return False, ERR_NOT_PENDING, False

    temp_password = generate_temp_password()
    auth_user.password_hash = _hash_password(temp_password)
    await auth_user.save()
    set_must_change_password(user_node, True)

    recipient = str(slot.get("recipient_email") or "")
    emailed = await _release_credentials_email(
        user_node=user_node, slot=slot, temp_password=temp_password
    )
    _mark_released(user_node, recipient_email=recipient)
    await user_node.save()

    logger.info(
        "credentials_delivery: released member_user_id=%s recipient=%s",
        user_node.id,
        recipient,
    )
    return True, None, emailed
