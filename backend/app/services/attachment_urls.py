"""Shared attachment URL resolution for API routes and entry context."""

from typing import Any, Dict, Optional

from jvspatial.api.constants import APIRoutes

from app.models.nodes import Attachment
from app.services.attachment_preview import can_preview
from app.services.attachment_scanner import SCAN_STATUS_BLOCKED
from app.services.attachment_storage import get_attachment_storage_service


def is_visible_to_user(attachment: Attachment) -> bool:
    """Whether an attachment should appear in user-facing list/get routes."""
    return getattr(attachment, "scan_status", "") != SCAN_STATUS_BLOCKED


async def resolve_download_url(attachment: Attachment) -> str:
    """Return the best download URL for ``attachment``.

    Order of preference:
        1. Provider-issued presigned URL (S3 / S3-compatible).
        2. The auth-gated streaming endpoint (local provider fallback).
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
