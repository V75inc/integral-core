"""Reusable Core contract for ownership of an anonymous form submission.

The plaintext capability is returned only when issued. Core stores only its
SHA-256 digest and scopes it to the issuing ShareLink, workspace, Track, form,
and stable submission ID. After the record is created, the same capability is
bound to exactly one Entry ID.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.models.public_submission_capability import PublicSubmissionCapability
from app.services.entry_write_scope import entry_write_scope
from app.utils.time import utc_now_iso

PUBLIC_SUBMISSION_CAPABILITY_REQUIRED_FIELD = "_public_submission_capability_required"
DEFAULT_CAPABILITY_TTL = timedelta(hours=24)
MAX_CAPABILITY_TTL = timedelta(hours=24)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_expired(value: str) -> bool:
    try:
        expires_at = datetime.fromisoformat(str(value or ""))
    except (TypeError, ValueError):
        return True
    if expires_at.tzinfo is None:
        return True
    return _now() >= expires_at


def _normalize_scope(
    *,
    workspace_id: str,
    track_id: str,
    form_id: str,
    share_link_id: str,
    submission_id: str,
) -> Dict[str, str]:
    return {
        "workspace_id": str(workspace_id or "").strip(),
        "track_id": str(track_id or "").strip(),
        "form_id": str(form_id or "").strip(),
        "share_link_id": str(share_link_id or "").strip(),
        "submission_id": str(submission_id or "").strip(),
    }


async def ensure_public_submission_capability_indexes() -> None:
    """Ensure the capability Object's lookup indexes exist."""
    from jvspatial.core.context import get_default_context

    await get_default_context().ensure_indexes(PublicSubmissionCapability)


async def issue_public_submission_capability(
    *,
    workspace_id: str,
    track_id: str,
    form_id: str,
    share_link_id: str,
    submission_id: str,
    ttl: timedelta = DEFAULT_CAPABILITY_TTL,
) -> Dict[str, str]:
    """Issue a one-time-disclosed capability for a pending public submission."""
    scope = _normalize_scope(
        workspace_id=workspace_id,
        track_id=track_id,
        form_id=form_id,
        share_link_id=share_link_id,
        submission_id=submission_id,
    )
    if not all(scope.values()):
        raise ValueError("All public submission capability scope fields are required")
    ttl = ttl if isinstance(ttl, timedelta) else DEFAULT_CAPABILITY_TTL
    if ttl > MAX_CAPABILITY_TTL:
        raise ValueError("Public submission capability TTL exceeds the Core limit")
    capability = secrets.token_urlsafe(32)
    now = _now()
    await ensure_public_submission_capability_indexes()
    row = await PublicSubmissionCapability.create(
        **scope,
        capability_hash=hashlib.sha256(capability.encode("utf-8")).hexdigest(),
        entry_id="",
        status="active",
        expires_at=(now + ttl).isoformat(),
        created_at=now.isoformat(),
        updated_at=now.isoformat(),
    )
    return {
        "capability_id": str(row.id),
        "capability": capability,
        "expires_at": row.expires_at,
    }


async def _find_capability(capability: str) -> Optional[PublicSubmissionCapability]:
    raw = str(capability or "")
    if not (32 <= len(raw) <= 256):
        return None
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    rows = await PublicSubmissionCapability.find({"context.capability_hash": digest})
    if len(rows or []) != 1:
        return None
    row = rows[0]
    if not secrets.compare_digest(str(row.capability_hash or ""), digest):
        return None
    return row


def _scope_matches(
    row: PublicSubmissionCapability,
    *,
    workspace_id: str,
    track_id: str,
    form_id: str,
    share_link_id: str,
    submission_id: str,
) -> bool:
    scope = _normalize_scope(
        workspace_id=workspace_id,
        track_id=track_id,
        form_id=form_id,
        share_link_id=share_link_id,
        submission_id=submission_id,
    )
    return all(getattr(row, key, "") == value for key, value in scope.items())


def _usable(row: PublicSubmissionCapability) -> bool:
    return bool(
        row.status == "active"
        and not row.revoked_at
        and not _is_expired(row.expires_at)
    )


async def validate_public_submission_capability(
    capability: str,
    *,
    workspace_id: str,
    track_id: str,
    form_id: str,
    share_link_id: str,
    submission_id: str,
    entry_id: str = "",
    capability_id: str = "",
) -> bool:
    """Validate secret, exact form/link/tenant scope, and pending/entry binding."""
    row = await _find_capability(capability)
    if row is None or not _usable(row):
        return False
    expected_id = str(capability_id or "").strip()
    if expected_id and row.id != expected_id:
        return False
    if not _scope_matches(
        row,
        workspace_id=workspace_id,
        track_id=track_id,
        form_id=form_id,
        share_link_id=share_link_id,
        submission_id=submission_id,
    ):
        return False
    expected_entry = str(entry_id or "").strip()
    return not expected_entry or row.entry_id == expected_entry


async def validate_public_entry_capability(
    capability: str,
    *,
    workspace_id: str,
    track_id: str,
    form_id: str,
    share_link_id: str,
    entry_id: str,
) -> bool:
    """Validate the bound-entry form of the contract for generic Core routes."""
    row = await _find_capability(capability)
    if row is None or not _usable(row) or not row.submission_id:
        return False
    if not _scope_matches(
        row,
        workspace_id=workspace_id,
        track_id=track_id,
        form_id=form_id,
        share_link_id=share_link_id,
        submission_id=row.submission_id,
    ):
        return False
    expected_entry = str(entry_id or "").strip()
    return bool(expected_entry and row.entry_id == expected_entry)


async def bind_public_submission_capability(
    capability: str,
    *,
    workspace_id: str,
    track_id: str,
    form_id: str,
    share_link_id: str,
    submission_id: str,
    entry_id: str,
) -> bool:
    """Bind an active pending capability to one resulting Entry, idempotently."""
    entry = str(entry_id or "").strip()
    row = await _find_capability(capability)
    if row is None or not entry:
        return False
    async with entry_write_scope(f"public-submission-capability:{row.id}"):
        row = await PublicSubmissionCapability.get(row.id)
        if row is None or not _usable(row):
            return False
        if not _scope_matches(
            row,
            workspace_id=workspace_id,
            track_id=track_id,
            form_id=form_id,
            share_link_id=share_link_id,
            submission_id=submission_id,
        ):
            return False
        if row.entry_id and row.entry_id != entry:
            return False
        row.entry_id = entry
        row.updated_at = utc_now_iso()
        await row.save()
        return True


async def revoke_public_submission_capability(
    capability_id: str,
    *,
    submission_id: str,
) -> bool:
    """Revoke a pending submission's capability; retain the tombstone for denial."""
    identifier = str(capability_id or "").strip()
    submission = str(submission_id or "").strip()
    if not (identifier and submission):
        return False
    async with entry_write_scope(f"public-submission-capability:{identifier}"):
        row = await PublicSubmissionCapability.get(identifier)
        if row is None or row.submission_id != submission:
            return False
        if row.status != "active":
            return bool(row.revoked_at)
        now = utc_now_iso()
        row.status = "revoked"
        row.revoked_at = now
        row.updated_at = now
        await row.save()
        return True


async def public_submission_capability_required(entry: Any) -> bool:
    """Read the generic opt-in marker set by a trusted public-submit host."""
    fields = getattr(entry, "custom_fields", None) or {}
    return fields.get(PUBLIC_SUBMISSION_CAPABILITY_REQUIRED_FIELD) is True
