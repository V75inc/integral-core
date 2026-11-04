"""Core-owned mailbox verification and single-use proof lifecycle."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from weakref import WeakValueDictionary

from app.config import settings
from app.models.mailbox_verification import MailboxVerificationProof
from app.services.email_service import EmailMessage, send_email
from app.services.entry_write_scope import entry_write_scope
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

VERIFIED_PROOF_LIFETIME_DAYS = 30

ERR_INVALID_CODE = "auth.mailbox_verification.code_invalid"
ERR_EXPIRED_CODE = "auth.mailbox_verification.code_expired"
ERR_ATTEMPTS_EXCEEDED = "auth.mailbox_verification.attempts_exceeded"
ERR_NOT_PENDING = "auth.mailbox_verification.not_pending"
ERR_PROOF_INVALID = "auth.mailbox_verification.proof_invalid"
ERR_PROOF_CONSUMED = "auth.mailbox_verification.proof_consumed"

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


def _record_subject_id(record: MailboxVerificationProof) -> str:
    return str(record.subject_entry_id or record.candidate_entry_id or "").strip()


async def _transition_proof(
    record: MailboxVerificationProof,
    changes: Dict[str, Any],
    *,
    invalidate: bool = False,
) -> bool:
    """Use the public conditional-update seam; a transaction alone is not a fence."""
    from jvspatial.core.context import get_default_context

    async with entry_write_scope(f"mailbox-verification-proof:{record.id}"):
        query: Dict[str, Any] = {"id": record.id, "entity": record._entity_name()}
        if not invalidate:
            # $in handles both missing legacy values and PostgreSQL JSON null.
            query["context.updated_at"] = {"$in": [record.updated_at]}
            # Fence the lifecycle itself as well: clock timestamps are not
            # guaranteed to advance between claims (or across workers).
            for field in (
                "consumed_at",
                "invalidated_at",
                "verified_at",
                "otp_attempts",
                "subject_entry_id",
                "candidate_entry_id",
                "otp_hash",
                "proof_expires_at",
            ):
                expected = getattr(record, field)
                values = [expected, None] if expected in ("", 0) else [expected]
                query[f"context.{field}"] = {"$in": values}
        changes = {**changes, "updated_at": utc_now_iso()}
        updated = await get_default_context().database.find_one_and_update(
            "object",
            query,
            {"$set": {f"context.{key}": value for key, value in changes.items()}},
        )
        if updated is None:
            return False
        # The claim holds the database row until this scope commits. Publish
        # that authoritative snapshot through Object.save so graph transaction
        # cache invalidation also reaches the parent/request context.
        fresh = MailboxVerificationProof.model_validate(
            {"id": record.id, **updated["context"]}
        )
        await fresh.save()
        for key in changes:
            setattr(record, key, getattr(fresh, key))
        return True


async def _find_subject_proofs(
    subject_entry_id: str,
    *,
    workspace_id: Optional[str] = None,
    purpose: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[MailboxVerificationProof]:
    """Read generic proofs and older records using the former subject field."""
    from jvspatial.core.context import get_default_context

    subject_id = str(subject_entry_id or "").strip()
    if not subject_id:
        return []
    scope: Dict[str, str] = {}
    if workspace_id:
        scope["workspace_id"] = str(workspace_id)
    if purpose:
        scope["purpose"] = str(purpose)
    queries = [
        {"subject_entry_id": subject_id, **scope},
        {"candidate_entry_id": subject_id, **scope},
    ]
    found: Dict[str, MailboxVerificationProof] = {}
    context = get_default_context()
    for query in queries:
        scoped = {f"context.{key}": value for key, value in query.items()}
        if limit is None:
            rows = await context.find(MailboxVerificationProof, scoped)
        else:
            # Object.find treats keyword arguments as fields, including limit.
            # Use the supported bounded page API and select newest proofs first.
            page, _ = await context.find_page(
                "object",
                {"entity": MailboxVerificationProof._entity_name(), **scoped},
                sort=[("context.created_at", -1), ("id", -1)],
                limit=limit,
            )
            rows = [
                MailboxVerificationProof.model_validate(
                    {"id": item["id"], **item["context"]}
                )
                for item in page
            ]
        for row in rows:
            found[str(row.id)] = row
    records = sorted(
        found.values(), key=lambda row: str(row.created_at or ""), reverse=True
    )
    return records[:limit] if limit is not None else records


async def invalidate_mailbox_verification_proofs(
    *,
    subject_entry_id: str,
    workspace_id: Optional[str] = None,
    purpose: Optional[str] = None,
    except_proof_id: Optional[str] = None,
) -> int:
    """Mark unconsumed proofs invalid (email change, resend, etc.)."""
    subject_id = str(subject_entry_id or "").strip()
    if not subject_id:
        return 0
    rows = await _find_subject_proofs(
        subject_id, workspace_id=workspace_id, purpose=purpose
    )
    count = 0
    now = utc_now_iso()
    for row in rows:
        if except_proof_id and row.id == except_proof_id:
            continue
        if row.invalidated_at:
            continue
        # Invalidation only sets its tombstone, never writes a stale snapshot
        # over a concurrently verified/consumed proof.
        if await _transition_proof(row, {"invalidated_at": now}, invalidate=True):
            count += 1
    return count


async def start_mailbox_verification(
    *,
    subject_entry_id: str,
    workspace_id: str,
    email: str,
    purpose: str,
) -> Dict[str, Any]:
    """Create OTP state and return plaintext code for outbound email only."""
    entry_id = str(subject_entry_id or "").strip()
    ws = str(workspace_id or "").strip()
    email_norm = _normalize_email(email)
    purpose_norm = str(purpose or "").strip()
    if not (entry_id and ws and email_norm and purpose_norm) or len(purpose_norm) > 128:
        return {"ok": False, "error": "missing_fields"}

    lock_key = f"mailbox:{ws}:{entry_id}:{purpose_norm}"
    lock = _RESEND_LOCKS.get(lock_key)
    if lock is None:
        lock = asyncio.Lock()
        _RESEND_LOCKS[lock_key] = lock
    async with lock:
        prior = await _find_subject_proofs(
            entry_id, workspace_id=ws, purpose=purpose_norm
        )
        if prior:
            latest = max(prior, key=lambda item: str(item.created_at or ""))
            # Changing the recipient must revoke every prior proof immediately,
            # even if the resend cooldown still prevents issuing a new code.
            if latest.email_norm != email_norm:
                await invalidate_mailbox_verification_proofs(
                    subject_entry_id=entry_id,
                    workspace_id=ws,
                    purpose=purpose_norm,
                )
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
        await invalidate_mailbox_verification_proofs(
            subject_entry_id=entry_id,
            workspace_id=ws,
            purpose=purpose_norm,
        )
        code, code_hash = _generate_otp()
        now = utc_now_iso()
        record = await MailboxVerificationProof.create(
            subject_entry_id=entry_id,
            workspace_id=ws,
            email_norm=email_norm,
            purpose=purpose_norm,
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
            "subject_entry_id": entry_id,
        }


async def deliver_mailbox_verification_email(
    *,
    email: str,
    code: str,
    workspace_id: str,
    subject_entry_id: str,
) -> Dict[str, Any]:
    """Send OTP email (no graph writes)."""
    to_addr = _normalize_email(email)
    otp = str(code or "").strip()
    if not (to_addr and otp):
        return {"ok": False, "error": "delivery_incomplete"}
    minutes = int(settings.EMAIL_VERIFICATION_CODE_EXPIRE_MINUTES)
    subject = "Confirm your email address"
    text = (
        f"Your confirmation code is {otp}.\n\n"
        f"It expires in {minutes} minutes.\n\n"
        "If you did not request a verification code, you can ignore this email."
    )
    html = (
        f"<p>Your confirmation code is <strong>{otp}</strong>.</p>"
        f"<p>It expires in {minutes} minutes.</p>"
        "<p>If you did not request a verification code, you can ignore this email.</p>"
    )
    emailed = await send_email(
        EmailMessage(
            to=to_addr,
            subject=subject,
            text=text,
            html=html,
            workspace_id=str(workspace_id or ""),
            source_kind="mailbox_verification",
            source_id=str(subject_entry_id or ""),
            actor_user_id="public",
        )
    )
    return {"ok": True, "emailed": bool(emailed), "verification_required": True}


async def confirm_mailbox_verification(
    *,
    subject_entry_id: str,
    workspace_id: str,
    purpose: str,
    code: str,
    proof_id: Optional[str] = None,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Validate OTP; return (ok, error_code, proof_id)."""
    entry_id = str(subject_entry_id or "").strip()
    ws = str(workspace_id or "").strip()
    purpose_norm = str(purpose or "").strip()
    if not (entry_id and ws and purpose_norm):
        return False, ERR_NOT_PENDING, None
    record: Optional[MailboxVerificationProof] = None
    pid = str(proof_id or "").strip()
    if pid:
        record = await MailboxVerificationProof.get(pid)
        if (
            record is None
            or _record_subject_id(record) != entry_id
            or record.workspace_id != ws
            or record.purpose != purpose_norm
        ):
            return False, ERR_NOT_PENDING, None
    else:
        rows = await _find_subject_proofs(
            entry_id,
            workspace_id=ws,
            purpose=purpose_norm,
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
    # Reserve an attempt before comparing a guess. Only successful conditional
    # claims get to test the OTP, so parallel requests cannot evade the limit.
    if not await _transition_proof(
        record,
        {
            "otp_attempts": attempts + 1,
            "subject_entry_id": entry_id,
            "candidate_entry_id": None,
        },
    ):
        return False, ERR_INVALID_CODE, None
    norm = str(code or "").strip()
    if len(norm) != 6 or not norm.isdigit():
        return False, ERR_INVALID_CODE, None
    digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()
    if not secrets.compare_digest(digest, str(record.otp_hash or "")):
        if record.otp_attempts >= max_attempts:
            return False, ERR_ATTEMPTS_EXCEEDED, None
        return False, ERR_INVALID_CODE, None
    now = utc_now_iso()
    if not await _transition_proof(
        record,
        {
            "verified_at": now,
            "proof_expires_at": (
                _now_utc() + timedelta(days=VERIFIED_PROOF_LIFETIME_DAYS)
            ).isoformat(),
            "otp_hash": "",
            "otp_expires_at": "",
        },
    ):
        return False, ERR_NOT_PENDING, None
    return True, None, record.id


def _proof_is_valid(
    record: MailboxVerificationProof, *, email_norm: str, purpose: str
) -> bool:
    if record.invalidated_at or record.consumed_at:
        return False
    if not record.verified_at:
        return False
    if not record.proof_expires_at or _is_expired(record.proof_expires_at):
        return False
    if record.email_norm != email_norm:
        return False
    if record.purpose != purpose:
        return False
    return True


async def rebind_mailbox_verification_proof(
    *,
    proof_id: str,
    source_subject_entry_id: str,
    subject_entry_id: str,
    workspace_id: str,
    email: str,
    purpose: str,
) -> Tuple[bool, Optional[str]]:
    """Move a proof from a temporary submission record to its final record.

    Public intake may retain its payload outside the final entry until the
    mailbox is verified. This server-only transition preserves the same proof
    while preventing callers from rebinding
    unverified, expired, consumed, cross-workspace, or mismatched-email proofs.
    """
    record = await MailboxVerificationProof.get(str(proof_id or "").strip())
    source_id = str(source_subject_entry_id or "").strip()
    target_id = str(subject_entry_id or "").strip()
    ws = str(workspace_id or "").strip()
    email_norm = _normalize_email(email)
    purpose_norm = str(purpose or "").strip()
    if not (record and source_id and target_id and ws and email_norm and purpose_norm):
        return False, ERR_PROOF_INVALID
    if (
        _record_subject_id(record) not in {source_id, target_id}
        or record.workspace_id != ws
    ):
        return False, ERR_PROOF_INVALID
    if not _proof_is_valid(record, email_norm=email_norm, purpose=purpose_norm):
        return False, ERR_PROOF_INVALID
    rebound = await _transition_proof(
        record, {"subject_entry_id": target_id, "candidate_entry_id": None}
    )
    return (True, None) if rebound else (False, ERR_PROOF_INVALID)


async def consume_mailbox_verification_proof(
    *,
    proof_id: str,
    subject_entry_id: str,
    workspace_id: str,
    personal_email: str,
    purpose: str,
    consumed_for_user_id: str,
) -> Tuple[bool, Optional[str]]:
    """Atomically consume a verified proof for account activation delivery."""
    pid = str(proof_id or "").strip()
    entry_id = str(subject_entry_id or "").strip()
    ws = str(workspace_id or "").strip()
    email_norm = _normalize_email(personal_email)
    purpose_norm = str(purpose or "").strip()
    user_id = str(consumed_for_user_id or "").strip()
    if not (pid and entry_id and ws and email_norm and purpose_norm and user_id):
        return False, ERR_PROOF_INVALID

    identity = f"mailbox-verification-proof:{pid}"
    async with entry_write_scope(identity):
        record = await MailboxVerificationProof.get(pid)
        if record is None:
            return False, ERR_PROOF_INVALID
        if _record_subject_id(record) != entry_id or record.workspace_id != ws:
            return False, ERR_PROOF_INVALID
        if record.consumed_at:
            return False, ERR_PROOF_CONSUMED
        if not _proof_is_valid(record, email_norm=email_norm, purpose=purpose_norm):
            return False, ERR_PROOF_INVALID
        now = utc_now_iso()
        consumed = await _transition_proof(
            record,
            {
                "subject_entry_id": entry_id,
                "candidate_entry_id": None,
                "consumed_at": now,
                "consumed_for_user_id": user_id,
            },
        )
        return (True, None) if consumed else (False, ERR_PROOF_INVALID)


async def proof_ready_for_account_provisioning(
    *,
    proof_id: str,
    subject_entry_id: str,
    workspace_id: str,
    personal_email: str,
    purpose: str,
) -> bool:
    """Non-mutating preflight before provisioning."""
    record = await MailboxVerificationProof.get(str(proof_id or "").strip())
    if record is None:
        return False
    if _record_subject_id(record) != str(subject_entry_id or "").strip():
        return False
    if record.workspace_id != str(workspace_id or "").strip():
        return False
    return _proof_is_valid(
        record,
        email_norm=_normalize_email(personal_email),
        purpose=str(purpose or "").strip(),
    )
