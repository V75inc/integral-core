"""Core-owned application mailbox OTP and hire-time proof consumption."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from weakref import WeakValueDictionary

from app.config import settings
from app.models.application_mailbox_verification import ApplicationMailboxVerification
from app.services.email_service import EmailMessage, send_email
from app.services.entry_write_scope import entry_write_scope
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

PURPOSE_RECRUITMENT = "recruitment_application"
VERIFIED_PROOF_LIFETIME_DAYS = 30

ERR_INVALID_CODE = "auth.application_mailbox.code_invalid"
ERR_EXPIRED_CODE = "auth.application_mailbox.code_expired"
ERR_ATTEMPTS_EXCEEDED = "auth.application_mailbox.attempts_exceeded"
ERR_NOT_PENDING = "auth.application_mailbox.not_pending"
ERR_PROOF_INVALID = "auth.application_mailbox.proof_invalid"
ERR_PROOF_CONSUMED = "auth.application_mailbox.proof_consumed"

_RESEND_INTERVAL_SECONDS = 60
_RESEND_LOCKS: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _expiry_iso() -> str:
    minutes = int(settings.EMAIL_VERIFICATION_CODE_EXPIRE_MINUTES)
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


def _normalize_email(email: str) -> str:
    return str(email or "").strip().casefold()


async def invalidate_application_mailbox_proofs(
    *,
    candidate_entry_id: str,
    except_proof_id: Optional[str] = None,
) -> int:
    """Mark unconsumed proofs invalid (email change, resend, etc.)."""
    entry_id = str(candidate_entry_id or "").strip()
    if not entry_id:
        return 0
    rows = await ApplicationMailboxVerification.find({"candidate_entry_id": entry_id})
    count = 0
    now = utc_now_iso()
    for row in rows:
        if except_proof_id and row.id == except_proof_id:
            continue
        if row.invalidated_at:
            continue
        row.invalidated_at = now
        row.updated_at = now
        await row.save()
        count += 1
    return count


async def start_application_mailbox_verification(
    *,
    candidate_entry_id: str,
    workspace_id: str,
    email: str,
    purpose: str = PURPOSE_RECRUITMENT,
) -> Dict[str, Any]:
    """Create OTP state and return plaintext code for outbound email only."""
    entry_id = str(candidate_entry_id or "").strip()
    ws = str(workspace_id or "").strip()
    email_norm = _normalize_email(email)
    if not (entry_id and ws and email_norm):
        return {"ok": False, "error": "missing_fields"}

    lock_key = f"app-mailbox:{entry_id}"
    lock = _RESEND_LOCKS.get(lock_key)
    if lock is None:
        lock = asyncio.Lock()
        _RESEND_LOCKS[lock_key] = lock
    async with lock:
        prior = await ApplicationMailboxVerification.find(
            {"candidate_entry_id": entry_id}
        )
        if prior:
            latest = max(prior, key=lambda item: str(item.created_at or ""))
            try:
                started_at = datetime.fromisoformat(str(latest.created_at or ""))
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                started_at = None
            if started_at is not None:
                elapsed = (_now_utc() - started_at).total_seconds()
                retry_after = int(_RESEND_INTERVAL_SECONDS - elapsed)
                if retry_after > 0:
                    return {
                        "ok": False,
                        "error": "rate_limited",
                        "retry_after_seconds": retry_after,
                    }
        await invalidate_application_mailbox_proofs(candidate_entry_id=entry_id)
        code, code_hash = _generate_otp()
        now = utc_now_iso()
        record = await ApplicationMailboxVerification.create(
            candidate_entry_id=entry_id,
            workspace_id=ws,
            email_norm=email_norm,
            purpose=purpose or PURPOSE_RECRUITMENT,
            otp_hash=code_hash,
            otp_expires_at=_expiry_iso(),
            otp_attempts=0,
            created_at=now,
            updated_at=now,
        )
        return {
            "ok": True,
            "proof_id": record.id,
            "email": email_norm,
            "code": code,
            "candidate_entry_id": entry_id,
        }


async def deliver_application_mailbox_verification_email(
    *,
    email: str,
    code: str,
    workspace_id: str,
    candidate_entry_id: str,
) -> Dict[str, Any]:
    """Send OTP email (no graph writes)."""
    to_addr = _normalize_email(email)
    otp = str(code or "").strip()
    if not (to_addr and otp):
        return {"ok": False, "error": "delivery_incomplete"}
    minutes = int(settings.EMAIL_VERIFICATION_CODE_EXPIRE_MINUTES)
    subject = "Confirm your job application email"
    text = (
        f"Your confirmation code is {otp}.\n\n"
        f"It expires in {minutes} minutes.\n\n"
        "If you did not apply for a job, you can ignore this email."
    )
    html = (
        f"<p>Your confirmation code is <strong>{otp}</strong>.</p>"
        f"<p>It expires in {minutes} minutes.</p>"
        "<p>If you did not apply for a job, you can ignore this email.</p>"
    )
    emailed = await send_email(
        EmailMessage(
            to=to_addr,
            subject=subject,
            text=text,
            html=html,
            workspace_id=str(workspace_id or ""),
            source_kind="application_mailbox_verification",
            source_id=str(candidate_entry_id or ""),
            actor_user_id="public",
        )
    )
    return {"ok": True, "emailed": bool(emailed), "verification_required": True}


async def confirm_application_mailbox_verification(
    *,
    candidate_entry_id: str,
    code: str,
    proof_id: Optional[str] = None,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Validate OTP; return (ok, error_code, proof_id)."""
    entry_id = str(candidate_entry_id or "").strip()
    if not entry_id:
        return False, ERR_NOT_PENDING, None
    record: Optional[ApplicationMailboxVerification] = None
    pid = str(proof_id or "").strip()
    if pid:
        record = await ApplicationMailboxVerification.get(pid)
        if record is None or record.candidate_entry_id != entry_id:
            return False, ERR_NOT_PENDING, None
    else:
        rows = await ApplicationMailboxVerification.find(
            {"candidate_entry_id": entry_id},
            limit=20,
        )
        pending = [
            r
            for r in rows
            if not r.consumed_at
            and not r.invalidated_at
            and r.verified_at is None
            and r.otp_hash
        ]
        if not pending:
            return False, ERR_NOT_PENDING, None
        record = sorted(pending, key=lambda r: r.created_at or "", reverse=True)[0]
    if record.invalidated_at or record.consumed_at or record.verified_at:
        return False, ERR_NOT_PENDING, None
    if not record.otp_hash:
        return False, ERR_NOT_PENDING, None
    if _is_expired(str(record.otp_expires_at or "")):
        return False, ERR_EXPIRED_CODE, None
    attempts = int(record.otp_attempts or 0)
    max_attempts = int(settings.EMAIL_VERIFICATION_MAX_ATTEMPTS)
    if attempts >= max_attempts:
        return False, ERR_ATTEMPTS_EXCEEDED, None
    norm = str(code or "").strip()
    if len(norm) != 6 or not norm.isdigit():
        record.otp_attempts = attempts + 1
        record.updated_at = utc_now_iso()
        await record.save()
        return False, ERR_INVALID_CODE, None
    digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()
    if not secrets.compare_digest(digest, str(record.otp_hash or "")):
        record.otp_attempts = attempts + 1
        record.updated_at = utc_now_iso()
        await record.save()
        if record.otp_attempts >= max_attempts:
            return False, ERR_ATTEMPTS_EXCEEDED, None
        return False, ERR_INVALID_CODE, None
    now = utc_now_iso()
    record.verified_at = now
    record.proof_expires_at = (
        _now_utc() + timedelta(days=VERIFIED_PROOF_LIFETIME_DAYS)
    ).isoformat()
    record.otp_hash = ""
    record.otp_expires_at = ""
    record.updated_at = now
    await record.save()
    return True, None, record.id


def _proof_is_valid(record: ApplicationMailboxVerification, *, email_norm: str) -> bool:
    if record.invalidated_at or record.consumed_at:
        return False
    if not record.verified_at:
        return False
    if not record.proof_expires_at or _is_expired(record.proof_expires_at):
        return False
    if record.email_norm != email_norm:
        return False
    return True


async def rebind_application_mailbox_proof(
    *,
    proof_id: str,
    pending_application_id: str,
    candidate_entry_id: str,
    workspace_id: str,
    email: str,
) -> Tuple[bool, Optional[str]]:
    """Bind a verified temporary application proof to its created candidate.

    Public application intake may retain its payload outside the Candidate
    graph until OTP succeeds. This server-only transition preserves the same
    proof for hire-time consumption while preventing callers from rebinding
    unverified, expired, consumed, cross-workspace, or mismatched-email proofs.
    """
    record = await ApplicationMailboxVerification.get(str(proof_id or "").strip())
    pending_id = str(pending_application_id or "").strip()
    candidate_id = str(candidate_entry_id or "").strip()
    ws = str(workspace_id or "").strip()
    email_norm = _normalize_email(email)
    if not (record and pending_id and candidate_id and ws and email_norm):
        return False, ERR_PROOF_INVALID
    if record.candidate_entry_id not in {pending_id, candidate_id} or record.workspace_id != ws:
        return False, ERR_PROOF_INVALID
    if not _proof_is_valid(record, email_norm=email_norm):
        return False, ERR_PROOF_INVALID
    record.candidate_entry_id = candidate_id
    record.updated_at = utc_now_iso()
    await record.save()
    return True, None


async def consume_application_mailbox_proof(
    *,
    proof_id: str,
    candidate_entry_id: str,
    workspace_id: str,
    personal_email: str,
    consumed_for_user_id: str,
) -> Tuple[bool, Optional[str]]:
    """Atomically consume a verified proof for hire credential delivery."""
    pid = str(proof_id or "").strip()
    entry_id = str(candidate_entry_id or "").strip()
    ws = str(workspace_id or "").strip()
    email_norm = _normalize_email(personal_email)
    user_id = str(consumed_for_user_id or "").strip()
    if not (pid and entry_id and ws and email_norm and user_id):
        return False, ERR_PROOF_INVALID

    identity = f"application-mailbox-proof:{pid}"
    async with entry_write_scope(identity):
        record = await ApplicationMailboxVerification.get(pid)
        if record is None:
            return False, ERR_PROOF_INVALID
        if record.candidate_entry_id != entry_id or record.workspace_id != ws:
            return False, ERR_PROOF_INVALID
        if record.consumed_at:
            return False, ERR_PROOF_CONSUMED
        if not _proof_is_valid(record, email_norm=email_norm):
            return False, ERR_PROOF_INVALID
        now = utc_now_iso()
        record.consumed_at = now
        record.consumed_for_user_id = user_id
        record.updated_at = now
        await record.save()
        return True, None


async def proof_ready_for_hire(
    *,
    proof_id: str,
    candidate_entry_id: str,
    workspace_id: str,
    personal_email: str,
) -> bool:
    """Non-mutating preflight before provisioning."""
    record = await ApplicationMailboxVerification.get(str(proof_id or "").strip())
    if record is None:
        return False
    if record.candidate_entry_id != str(candidate_entry_id or "").strip():
        return False
    if record.workspace_id != str(workspace_id or "").strip():
        return False
    return _proof_is_valid(record, email_norm=_normalize_email(personal_email))
