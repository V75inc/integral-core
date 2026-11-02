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
    render_account_activation_email,
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


def _has_verified_mailbox_proof(value: Dict[str, Any]) -> bool:
    """Read the current flag and the former persisted key during migration."""
    return bool(
        value.get("mailbox_verification_proof") or value.get("application_proof")
    )


def _verified_mailbox_slot(slot: Dict[str, Any]) -> bool:
    return (
        isinstance(slot, dict)
        and slot.get("version") == 3
        and _has_verified_mailbox_proof(slot)
    )


def _activation_failure_category(
    *, success: bool, status: Optional[str], error: str
) -> str:
    if success:
        return "accepted"
    normalized = str(status or "").strip().lower()
    err = str(error or "").strip().lower()
    if normalized == "suppressed" or "suppress" in err:
        return "suppressed"
    if normalized == "failed":
        return "failed"
    return "pending_retry"


def activation_delivery_public_status(user_node: User) -> Dict[str, Any]:
    """Safe activation state for workspace admins (verified-mailbox path only)."""
    prefs = user_node.preferences or {}
    released = prefs.get(_PREFS_RELEASE) or {}
    if isinstance(released, dict) and released.get("at"):
        if _has_verified_mailbox_proof(released):
            return {
                "state": "already_released",
                "recipient_email": released.get("recipient_email"),
                "intent_id": None,
                "send_attempt_count": int(released.get("send_attempt_count") or 0),
                "last_attempt_at": released.get("at"),
                "provider_message_id": released.get("provider_message_id"),
            }
    slot = dict(prefs.get(_PREFS_PENDING) or {})
    if not _verified_mailbox_slot(slot):
        return {"state": "not_pending", "send_attempt_count": 0}
    if _is_expired(str(slot.get("expires_at") or "")):
        return {
            "state": "expired",
            "recipient_email": slot.get("recipient_email"),
            "intent_id": slot.get("intent_id"),
            "send_attempt_count": int(slot.get("send_attempt_count") or 0),
            "last_attempt_at": slot.get("last_send_attempt_at"),
        }
    category = str(slot.get("last_failure_category") or "")
    if category == "suppressed":
        state = "suppressed"
    elif slot.get("state") == "sending" and not _is_expired(
        str(slot.get("lease_until") or "")
    ):
        state = "sending"
    else:
        state = "pending_retry"
    return {
        "state": state,
        "recipient_email": slot.get("recipient_email"),
        "intent_id": slot.get("intent_id"),
        "send_attempt_count": int(slot.get("send_attempt_count") or 0),
        "last_attempt_at": slot.get("last_send_attempt_at"),
        "provider_message_id": slot.get("last_provider_message_id"),
    }


def delivery_status(user_node: User) -> Dict[str, Any]:
    """Snapshot for admin polling after alternate-mailbox provision."""
    prefs = user_node.preferences or {}
    released = prefs.get(_PREFS_RELEASE) or {}
    if isinstance(released, dict) and released.get("at"):
        base = {
            "pending_verification": False,
            "credentials_emailed": True,
            "released_at": released.get("at"),
            "recipient_email": released.get("recipient_email"),
        }
        if _has_verified_mailbox_proof(released):
            base["credentials_activation_emailed"] = True
            base.update(activation_delivery_public_status(user_node))
        return base
    slot = prefs.get(_PREFS_PENDING) or {}
    if isinstance(slot, dict) and _verified_mailbox_slot(slot):
        public = activation_delivery_public_status(user_node)
        retryable = public.get("state") in ("pending_retry", "suppressed", "sending")
        return {
            "pending_verification": False,
            "credentials_emailed": False,
            "credentials_activation_emailed": False,
            "credentials_delivery_retryable": retryable,
            "expires_at": slot.get("expires_at"),
            "recipient_email": slot.get("recipient_email"),
            "intent_id": slot.get("intent_id"),
            "expired": public.get("state") == "expired",
            **public,
        }
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


async def stage_verified_mailbox_activation_delivery(
    *,
    user_node: User,
    recipient_email: str,
    account_email: str,
    workspace_id: str,
    display_name: str,
    actor_user_id: str,
    proof_id: str,
    subject_entry_id: str,
    purpose: str,
) -> Dict[str, Any]:
    """Persist activation state and consume its proof in the caller's transaction."""
    from app.services.credential_crypto import encrypt_secret_for_storage
    from app.services.entry_write_scope import entry_write_scope
    from app.services.password_reset import _store_token_on_user, generate_reset_token

    require_delivery_storage()
    recipient_norm = recipient_email.strip().lower()
    account_norm = account_email.strip().lower()
    if not recipient_norm or recipient_norm == account_norm:
        raise ValueError("verified mailbox delivery requires distinct recipient")

    intent_id = secrets.token_urlsafe(16)
    identity = f"credentials-delivery:{user_node.id}"
    attempt_id = secrets.token_urlsafe(16)
    aad = f"credentials-delivery:{user_node.id}:{intent_id}"
    token: str
    token_hash_to_persist: Optional[str] = None
    slot: Dict[str, Any]
    async with entry_write_scope(identity):
        user = await _fresh_user(user_node.id)
        if user is None:
            raise ValueError("Account unavailable")
        await _lock_identity(user)
        user = await _fresh_user(user_node.id)
        if user is None:
            raise ValueError("Account unavailable")
        from app.services.authentication import get_authentication_service

        auth = await get_authentication_service()._get_user_by_id(user.user_id)
        if auth is None:
            raise ValueError("Account unavailable")
        prefs = dict(user.preferences or {})
        prefs.pop(_PREFS_RELEASE, None)
        existing = dict(prefs.get(_PREFS_PENDING) or {})
        if (
            _verified_mailbox_slot(existing)
            and existing.get("intent_id")
            and existing.get("state") in ("ready", "sending", None)
            and existing.get("recipient_email") == recipient_norm
            and existing.get("setup_token_enc")
        ):
            intent_id = str(existing["intent_id"])
            aad = f"credentials-delivery:{user.id}:{intent_id}"
            slot = existing
            from app.services.credential_crypto import decrypt_secret_from_storage

            token = decrypt_secret_from_storage(str(slot["setup_token_enc"]), aad=aad)
            current_token = (user.preferences or {}).get("reset_token") or {}
            if not token or current_token.get("purpose") != "account_activation":
                token, token_hash = generate_reset_token()
                token_hash_to_persist = token_hash
                slot["setup_token_enc"] = encrypt_secret_for_storage(token, aad=aad)
                slot["setup_token_hash"] = token_hash
        else:
            token, token_hash = generate_reset_token()
            token_hash_to_persist = token_hash
            slot = {
                "version": 3,
                "mailbox_verification_proof": True,
                "intent_id": intent_id,
                "recipient_email": recipient_norm,
                "account_email": account_norm,
                "workspace_id": workspace_id,
                "display_name": display_name,
                "actor_user_id": actor_user_id,
                "credential_version": hashlib.sha256(
                    str(auth.password_hash).encode()
                ).hexdigest(),
                "setup_token_enc": encrypt_secret_for_storage(token, aad=aad),
                "setup_token_hash": token_hash,
                "expires_at": _expiry_iso(),
                "state": "sending",
                "attempt_id": attempt_id,
                "lease_until": (_now_utc() + timedelta(seconds=60)).isoformat(),
            }
        slot.update(
            state="sending",
            attempt_id=attempt_id,
            lease_until=(_now_utc() + timedelta(seconds=60)).isoformat(),
        )
        prefs[_PREFS_PENDING] = slot
        user.preferences = prefs
        if token_hash_to_persist:
            _store_token_on_user(
                user, token_hash_to_persist, purpose="account_activation"
            )
        user.updated_at = _now_utc().isoformat()
        await user.save()

        from app.services.mailbox_verification import (
            consume_mailbox_verification_proof,
        )

        consumed, _error = await consume_mailbox_verification_proof(
            proof_id=proof_id,
            subject_entry_id=subject_entry_id,
            workspace_id=workspace_id,
            personal_email=recipient_norm,
            purpose=purpose,
            consumed_for_user_id=user.id,
        )
        if not consumed:
            from app.api.errors import BadRequestError

            raise BadRequestError(
                message="Mailbox verification proof could not be consumed"
            )

    return {
        "member_user_id": user_node.id,
        "recipient_email": recipient_norm,
        "intent_id": intent_id,
        "attempt_id": attempt_id,
        "slot": slot,
        "setup_token": token,
    }


async def deliver_staged_verified_mailbox_activation_link(
    prepared: Dict[str, Any],
) -> Dict[str, Any]:
    """Send a previously staged activation link after its transaction commits."""
    from app.services.permissions import get_user_node

    member_user_id = str(prepared.get("member_user_id") or "").strip()
    recipient_norm = str(prepared.get("recipient_email") or "").strip()
    intent_id = str(prepared.get("intent_id") or "").strip()
    attempt_id = str(prepared.get("attempt_id") or "").strip()
    slot = prepared.get("slot")
    token = str(prepared.get("setup_token") or "")
    if not (
        member_user_id
        and recipient_norm
        and intent_id
        and attempt_id
        and isinstance(slot, dict)
        and token
    ):
        raise ValueError("Verified mailbox activation stage is invalid")
    user_node = await get_user_node(member_user_id)
    if user_node is None:
        raise ValueError("Account unavailable")

    emailed, category, provider_message_id = (
        await _dispatch_verified_mailbox_activation_send(
            user_node=user_node, slot=slot, setup_token=token
        )
    )

    finalized = await _finalize_verified_mailbox_activation_send(
        member_user_id=member_user_id,
        intent_id=intent_id,
        attempt_id=attempt_id,
        recipient_norm=recipient_norm,
        emailed=emailed,
        failure_category=category,
        provider_message_id=provider_message_id,
    )
    finalized.update(
        intent_id=intent_id,
        challenge_emailed=False,
        expires_at=slot.get("expires_at"),
    )
    return finalized


async def _dispatch_verified_mailbox_activation_send(
    *,
    user_node: User,
    slot: Dict[str, Any],
    setup_token: str,
) -> Tuple[bool, str, Optional[str]]:
    try:
        return await _release_credentials_email(
            user_node=user_node, slot=slot, setup_token=setup_token
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "credentials_delivery: verified mailbox send failed member=%s",
            user_node.id,
        )
        return False, "pending_retry", None


async def _finalize_verified_mailbox_activation_send(
    *,
    member_user_id: str,
    intent_id: str,
    attempt_id: str,
    recipient_norm: str,
    emailed: bool,
    failure_category: str,
    provider_message_id: Optional[str],
) -> Dict[str, Any]:
    from app.services.entry_write_scope import entry_write_scope

    identity = f"credentials-delivery:{member_user_id}"
    now = _now_utc().isoformat()
    async with entry_write_scope(identity):
        fresh = await _fresh_user(member_user_id)
        if fresh is None:
            return {
                "activation_emailed": False,
                "delivery_retryable": True,
                "activation_state": "pending_retry",
            }
        await _lock_identity(fresh)
        fresh = await _fresh_user(member_user_id)
        if fresh is None:
            return {
                "activation_emailed": False,
                "delivery_retryable": True,
                "activation_state": "pending_retry",
            }
        current = dict((fresh.preferences or {}).get(_PREFS_PENDING) or {})
        if not _verified_mailbox_slot(current):
            released = (fresh.preferences or {}).get(_PREFS_RELEASE) or {}
            return {
                "activation_emailed": bool(released.get("at")),
                "delivery_retryable": not bool(released.get("at")),
                "activation_state": (
                    "already_released" if released.get("at") else "not_pending"
                ),
            }
        if (
            current.get("intent_id") != intent_id
            or current.get("attempt_id") != attempt_id
        ):
            released = (fresh.preferences or {}).get(_PREFS_RELEASE) or {}
            return {
                "activation_emailed": bool(released.get("at")),
                "delivery_retryable": not bool(released.get("at")),
                "activation_state": (
                    "already_released" if released.get("at") else "sending"
                ),
            }
        attempts = int(current.get("send_attempt_count") or 0) + 1
        current["send_attempt_count"] = attempts
        current["last_send_attempt_at"] = now
        if provider_message_id:
            current["last_provider_message_id"] = provider_message_id
        if emailed:
            fresh.preferences = {
                **(fresh.preferences or {}),
                _PREFS_RELEASE: {
                    "at": now,
                    "recipient_email": recipient_norm,
                    "mailbox_verification_proof": True,
                    "send_attempt_count": attempts,
                    "provider_message_id": provider_message_id,
                },
                _PREFS_PENDING: {},
            }
            state = "sent"
        else:
            current["state"] = "ready"
            current["last_failure_category"] = failure_category
            fresh.preferences = {**(fresh.preferences or {}), _PREFS_PENDING: current}
            state = (
                "suppressed" if failure_category == "suppressed" else "pending_retry"
            )
        fresh.updated_at = now
        await fresh.save()
    return {
        "activation_emailed": emailed,
        "delivery_retryable": not emailed,
        "activation_state": state,
        "send_attempt_count": attempts,
    }


async def retry_verified_mailbox_activation_delivery(
    *,
    workspace_id: str,
    member_user_id: str,
    actor_user_id: str,
    intent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Authorized admin retry for pending verified-mailbox activation mail."""
    from app.services.authentication import get_authentication_service
    from app.services.credential_crypto import (
        decrypt_secret_from_storage,
        encrypt_secret_for_storage,
    )
    from app.services.entry_write_scope import entry_write_scope
    from app.services.password_reset import _store_token_on_user, generate_reset_token
    from app.services.workspace_permissions import read_member_facts

    require_delivery_storage()
    ws = str(workspace_id or "").strip()
    member_id = str(member_user_id or "").strip()
    actor = str(actor_user_id or "").strip()
    if not (ws and member_id and actor):
        raise ValueError("workspace, member and actor are required")
    member = await _fresh_user(member_id)
    if member is None or await read_member_facts(member, ws) is None:
        raise ValueError("Member is not in this workspace")
    identity = f"credentials-delivery:{member_id}"
    attempt_id = secrets.token_urlsafe(16)
    token: Optional[str] = None
    slot: Dict[str, Any] = {}
    resolved_intent = str(intent_id or "").strip()
    async with entry_write_scope(identity):
        user = await _fresh_user(member_id)
        if user is None:
            return activation_delivery_public_status(member)  # type: ignore[return-value]
        await _lock_identity(user)
        user = await _fresh_user(member_id)
        if user is None:
            return {"state": "not_pending", "send_attempt_count": 0}
        prefs = dict(user.preferences or {})
        released = prefs.get(_PREFS_RELEASE) or {}
        if isinstance(released, dict) and released.get("at"):
            return activation_delivery_public_status(user)
        slot = dict(prefs.get(_PREFS_PENDING) or {})
        if not _verified_mailbox_slot(slot):
            return activation_delivery_public_status(user)
        if resolved_intent and slot.get("intent_id") != resolved_intent:
            return {"state": "not_pending", "send_attempt_count": 0}
        resolved_intent = str(slot.get("intent_id") or "")
        if _is_expired(str(slot.get("expires_at") or "")):
            return {"state": "expired", "intent_id": resolved_intent}
        if slot.get("state") == "sending" and not _is_expired(
            str(slot.get("lease_until") or "")
        ):
            return activation_delivery_public_status(user)
        if str(slot.get("workspace_id") or "") != ws:
            return {"state": "not_pending", "send_attempt_count": 0}
        auth = await get_authentication_service()._get_user_by_id(user.user_id)
        if auth is None or not getattr(auth, "is_active", True):
            return {"state": "not_pending", "send_attempt_count": 0}
        if hashlib.sha256(str(auth.password_hash).encode()).hexdigest() != slot.get(
            "credential_version"
        ):
            return {"state": "not_pending", "send_attempt_count": 0}
        aad = f"credentials-delivery:{member_id}:{resolved_intent}"
        encrypted = str(slot.get("setup_token_enc") or "")
        if not encrypted.startswith("v1:"):
            return {"state": "not_pending", "send_attempt_count": 0}
        token = decrypt_secret_from_storage(encrypted, aad=aad)
        current = (user.preferences or {}).get("reset_token") or {}
        if current.get("hash") != slot.get("setup_token_hash"):
            return {"state": "not_pending", "send_attempt_count": 0}
        if current.get("purpose") not in (None, "account_activation"):
            return {"state": "not_pending", "send_attempt_count": 0}
        if not token or _is_expired(str(current.get("expires_at") or "")):
            return {"state": "expired", "intent_id": resolved_intent}
        if current.get("purpose") is None:
            token, token_hash = generate_reset_token()
            _store_token_on_user(user, token_hash, purpose="account_activation")
            slot.update(
                setup_token_enc=encrypt_secret_for_storage(token, aad=aad),
                setup_token_hash=token_hash,
            )
        slot.update(
            state="sending",
            attempt_id=attempt_id,
            lease_until=(_now_utc() + timedelta(seconds=60)).isoformat(),
        )
        user.preferences = {**prefs, _PREFS_PENDING: slot}
        user.updated_at = _now_utc().isoformat()
        await user.save()

    if not token:
        return {"state": "not_pending", "send_attempt_count": 0}
    emailed, category, provider_message_id = (
        await _dispatch_verified_mailbox_activation_send(
            user_node=member, slot=slot, setup_token=token
        )
    )
    finalized = await _finalize_verified_mailbox_activation_send(
        member_user_id=member_id,
        intent_id=resolved_intent,
        attempt_id=attempt_id,
        recipient_norm=str(slot.get("recipient_email") or ""),
        emailed=emailed,
        failure_category=category,
        provider_message_id=provider_message_id,
    )
    public = activation_delivery_public_status(await _fresh_user(member_id) or member)
    public["activation_emailed"] = finalized.get("activation_emailed")
    public["delivery_retryable"] = finalized.get("delivery_retryable")
    return public


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
) -> Tuple[bool, str, Optional[str]]:
    """Deliver a password-setup link without changing the account password."""
    from app.services.email_service import send_email_with_outcome

    if _has_verified_mailbox_proof(slot):
        message = render_account_activation_email(
            recipient_email=str(slot["recipient_email"]),
            recipient_name=str(slot.get("display_name") or ""),
            account_email=str(slot.get("account_email") or ""),
            activation_url=f"{settings.APP_BASE_URL.rstrip('/')}/activate-account?token={quote(setup_token, safe='')}",
            expires_minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES,
        )
    else:
        message = render_password_reset_email(
            recipient_email=str(slot["recipient_email"]),
            recipient_name=str(slot.get("display_name") or ""),
            reset_url=f"{settings.APP_BASE_URL.rstrip('/')}/reset-password?token={quote(setup_token, safe='')}",
            expires_minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES,
        )
    # Authentication mail must use the platform provider, never an App hook.
    message.source_kind = (
        "account_activation" if _has_verified_mailbox_proof(slot) else "password_reset"
    )
    message.workspace_id = str(slot.get("workspace_id") or "")
    message.source_id = str(user_node.id or "")
    message.actor_user_id = str(slot.get("actor_user_id") or "")
    outcome = await send_email_with_outcome(message)
    category = _activation_failure_category(
        success=outcome.success,
        status=outcome.status,
        error=str(outcome.error or ""),
    )
    return outcome.success, category, outcome.provider_message_id


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
            _store_token_on_user(user, token_hash, purpose="password_reset")
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
    emailed, _, _ = await _dispatch_verified_mailbox_activation_send(
        user_node=user, slot=slot, setup_token=token
    )
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
