"""Shared single-file upload helpers used by both entry- and chat-scoped
attachment upload paths (``app/api/attachments.py`` and the chat-upload
endpoint, Slice B).

Extracted so the chat-upload path (Task B2) can reuse the same MIME
allow-list, streaming reader, and sniff-reconciliation logic without
duplicating it or importing module-private names across files.
"""

from __future__ import annotations

import hashlib
import os
from typing import List, Optional, Tuple

from fastapi import UploadFile

from app.api.errors import BadRequestError
from app.services.attachment_content_sniffer import sniff_bytes

UPLOAD_READ_CHUNK = 1024 * 1024

ALLOWED_MIME_PREFIXES = ("image/", "video/", "audio/")
ALLOWED_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "text/plain",
        "text/csv",
        "text/markdown",
        "application/json",
        "application/zip",
        "application/x-zip-compressed",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
    }
)

FILENAME_EXT_TO_MIME = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".md": "text/markdown",
    ".json": "application/json",
    ".zip": "application/zip",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def is_mime_allowed(mime: str) -> bool:
    """Return True when ``mime`` matches the allow-list or an allowed prefix."""
    return mime in ALLOWED_MIME_TYPES or any(
        mime.startswith(p) for p in ALLOWED_MIME_PREFIXES
    )


def fallback_mime_from_filename(filename: str) -> Optional[str]:
    """Return the MIME type implied by ``filename``'s extension, if known."""
    ext = os.path.splitext(filename or "")[1].lower()
    return FILENAME_EXT_TO_MIME.get(ext)


async def read_upload_streaming(
    file: UploadFile, *, max_bytes: int
) -> Tuple[bytes, str, str, bytes]:
    """Read a multipart upload chunk-by-chunk under ``max_bytes``.

    Returns:
        content: Full byte buffer of the upload.
        filename: Best-effort filename from the upload (sanitized later
            by the storage layer).
        sha256_hex: Incremental SHA-256 over the bytes, hex-encoded.
        head_bytes: First chunk (used downstream for MIME sniffing —
            avoids a second pass over the full buffer for large files).
    """
    chunks: List[bytes] = []
    head: Optional[bytes] = None
    total = 0
    hasher = hashlib.sha256()
    while True:
        chunk = await file.read(UPLOAD_READ_CHUNK)
        if not chunk:
            break
        if head is None:
            head = chunk[:4096]
        total += len(chunk)
        if total > max_bytes:
            raise BadRequestError(
                message=(
                    f"File too large (max {max_bytes // (1024 * 1024)}MB). "
                    "Use the chunked-upload endpoint for larger files."
                ),
            )
        hasher.update(chunk)
        chunks.append(chunk)
    content = b"".join(chunks)
    name = file.filename or "unnamed"
    return content, name, hasher.hexdigest(), head or b""


def resolve_effective_mime(
    *,
    head_bytes: bytes,
    content_type_claim: str,
    filename: str,
) -> str:
    """Sniff the file's MIME and reconcile with the client's claim.

    - When libmagic is available, the sniffed type is authoritative.
    - When the claim disagrees with the sniff, raise BadRequestError.
    - When neither claim nor sniff yields an allow-listed type but the
      filename extension does, use the extension-derived MIME (covers
      mobile clients that send ``application/octet-stream`` for safe
      file types).
    """
    claim = (content_type_claim or "").split(";", 1)[0].strip().lower()
    sniff = sniff_bytes(
        head_bytes,
        claimed_mime=claim,
        filename=filename,
    )
    if sniff.mismatch:
        raise BadRequestError(
            message=(
                "File type does not match its content. "
                f"Client claimed {claim or 'unknown'}; "
                f"server detected {sniff.sniffed_mime or 'unknown'}."
            ),
        )

    candidate = sniff.effective_mime or claim
    if is_mime_allowed(candidate):
        return candidate

    # Sniff/claim came back as the generic octet-stream — try the
    # filename extension as a last resort. Same allow-list applies.
    fallback = fallback_mime_from_filename(filename)
    if fallback and is_mime_allowed(fallback):
        return fallback

    raise BadRequestError(
        message="File type not allowed. Use common documents, images, audio, or video.",
    )
