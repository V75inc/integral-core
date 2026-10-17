"""Shared attachment URL resolution for API routes and entry context."""

import hashlib
import hmac
import time
from typing import Any, Dict, Optional

from jvspatial.api.constants import APIRoutes

from app.models.nodes import Attachment
from app.services.attachment_preview import can_preview
from app.services.attachment_scanner import SCAN_STATUS_BLOCKED
from app.services.attachment_storage import get_attachment_storage_service

#: Lifetime of a session-free signed attachment URL (seconds).
_ATTACHMENT_URL_TTL_SECONDS = 3600


def is_visible_to_user(attachment: Attachment) -> bool:
    """Whether an attachment should appear in user-facing list/get routes."""
    return getattr(attachment, "scan_status", "") != SCAN_STATUS_BLOCKED


def _token_secret() -> bytes:
    from app.config import settings

    return str(settings.SECRET_KEY or "").encode("utf-8")


def _sign_attachment(attachment_id: str, exp: int) -> str:
    return hmac.new(
        _token_secret(),
        f"integral-attachment-v1:{attachment_id}:{exp}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def mint_attachment_token(
    attachment_id: str, ttl: int = _ATTACHMENT_URL_TTL_SECONDS
) -> str:
    """Return the signed ``exp=…&sig=…`` query for a session-free attachment URL."""
    exp = int(time.time()) + int(ttl)
    return f"exp={exp}&sig={_sign_attachment(attachment_id, exp)}"


def verify_attachment_token(attachment_id: str, exp: Any, sig: Any) -> bool:
    """Verify a token from :func:`mint_attachment_token` (unexpired, unforged)."""
    try:
        exp_int = int(str(exp))
    except (TypeError, ValueError):
        return False
    if exp_int < int(time.time()):
        return False
    return hmac.compare_digest(
        _sign_attachment(attachment_id, exp_int).encode("ascii"),
        str(sig or "").encode("utf-8"),
    )


def public_attachment_url(attachment: Attachment) -> str:
    """A session-free, signed, expiring URL that streams the attachment bytes.

    Served by Core's own attachment storage (``/attachments/{id}/inline``), so it
    is independent of the jvspatial storage interface the channel adapter can't
    reach with a session. The signed token is the credential; it expires.
    """
    return (
        f"{APIRoutes.PREFIX}/attachments/{attachment.id}/inline"
        f"?{mint_attachment_token(attachment.id)}"
    )


async def resolve_download_url(attachment: Attachment) -> str:
    """Return the best download URL for ``attachment``.

    Order of preference:
        1. Provider-issued presigned URL (S3 / S3-compatible) — session-free.
        2. A Core-served signed ``/attachments/{id}/inline`` URL — session-free for
           the local provider (so channel adapters that fetch with no session succeed).
        3. The auth-gated streaming endpoint (last-resort fallback).
    """
    if attachment.storage_key:
        try:
            storage = get_attachment_storage_service()
            signed = await storage.get_signed_url(
                attachment.storage_key,
                download_filename=attachment.filename or None,
            )
            if signed:
                return signed
        except Exception:  # noqa: BLE001
            pass
        return public_attachment_url(attachment)
    return f"{APIRoutes.PREFIX}/attachments/{attachment.id}/download"


def thumb_url_for(attachment: Attachment) -> Optional[str]:
    """Public URL for the cached thumbnail, or None when no thumb exists."""
    if attachment.source_type != "file":
        return None
    if not attachment.thumb_storage_key:
        return None
    return f"{APIRoutes.PREFIX}/attachments/{attachment.id}/thumb"


def preview_url_for(attachment: Attachment) -> Optional[str]:
    """Public URL for the cached preview PDF, or None for non-previewable."""
    if attachment.source_type != "file":
        return None
    if not can_preview(attachment.mime_type):
        return None
    return f"{APIRoutes.PREFIX}/attachments/{attachment.id}/preview"


async def enrich_attachment_export(
    item: Dict[str, Any], attachment: Attachment
) -> Dict[str, Any]:
    """Add download_url, thumb_url, and preview_url to an exported attachment."""
    if attachment.source_type == "url" and attachment.external_url:
        item["download_url"] = attachment.external_url
    else:
        item["download_url"] = await resolve_download_url(attachment)
    item["thumb_url"] = thumb_url_for(attachment)
    item["preview_url"] = preview_url_for(attachment)
    return item
