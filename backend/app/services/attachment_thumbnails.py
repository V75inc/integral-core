"""Image thumbnail generation for attachments.

Generates a max-512px-long-edge JPEG sibling next to the original blob:

    attachments/{entry_id}/{attachment_id}/<filename>      # original
    attachments/{entry_id}/{attachment_id}/_thumb.jpg      # generated

The thumb is stored under a stable sibling key so we don't need a
separate node — the parent Attachment.thumb_storage_key points at it.
A sibling lifecycle also gives us "delete the attachment folder and
everything we generated is gone" for free.

Pillow is soft-imported: when it's unavailable (slim CI containers,
weird ARM wheels) thumbnail generation is skipped, attachment uploads
still succeed, and the GET /attachments/{id}/thumb endpoint returns
404. ``ensure_thumbnail()`` is idempotent — call it again to re-generate
after Pillow gets installed.
"""

from __future__ import annotations

import io
import logging
from typing import Optional

from app.models.nodes import Attachment
from app.services.attachment_storage import (
    AttachmentStorageService,
    get_attachment_storage_service,
)

logger = logging.getLogger(__name__)

# Long-edge cap for the generated JPEG. 512 px is large enough to read
# image content at typical UI sizes without blowing up storage cost or
# generation latency.
THUMB_MAX_LONG_EDGE = 512
THUMB_FORMAT = "JPEG"
THUMB_QUALITY = 82
THUMB_FILENAME = "_thumb.jpg"


def _can_thumbnail(mime: str) -> bool:
    """Whether the MIME type is a candidate for raster thumbnail."""
    if not mime:
        return False
    # SVG renders fine without a raster thumb — the original is already
    # browser-native and dependency-free.
    if mime in {"image/svg+xml"}:
        return False
    return mime.startswith("image/")


async def ensure_thumbnail(
    attachment: Attachment,
    *,
    entry_id: str,
    content: Optional[bytes] = None,
    storage: Optional[AttachmentStorageService] = None,
) -> Optional[str]:
    """Generate (or regenerate) the thumbnail for ``attachment``.

    Returns the sibling storage key when a thumbnail was produced and
    persisted; ``None`` when the attachment isn't thumbnailable or when
    Pillow isn't available.

    Args:
        attachment: Target attachment record. Updated in place with
            ``thumb_storage_key`` on success (the caller is responsible
            for ``await attachment.save()`` afterwards).
        entry_id: Owning entry id; needed to build the sibling key.
        content: Optional original bytes. When omitted, the bytes are
            fetched from storage. Pass in when you have them already
            (upload flow) to avoid a redundant round trip.
        storage: Optional storage facade override (tests).
    """
    if attachment.source_type != "file":
        return None
    if not _can_thumbnail(attachment.mime_type):
        return None
    if not attachment.storage_key:
        return None

    try:
        from PIL import Image  # type: ignore[import-not-found]
    except Exception as e:  # noqa: BLE001
        logger.debug("Pillow unavailable; skipping thumbnail (%s)", e)
        return None

    svc = storage or get_attachment_storage_service()

    if content is None:
        content = await svc.read_attachment(attachment.storage_key)
        if content is None:
            return None

    # Pillow ≥9.1 exposes ``Image.Resampling.LANCZOS``; older releases
    # still in the wild only have ``Image.LANCZOS``. Resolve once and
    # let either path through so the thumbnailer doesn't crash on a
    # CI box that lags the latest Pillow release.
    try:
        resample_filter = Image.Resampling.LANCZOS
    except AttributeError:
        resample_filter = getattr(Image, "LANCZOS", 1)  # type: ignore[arg-type] # Pillow stub-version drift

    try:
        with Image.open(io.BytesIO(content)) as im:
            im.thumbnail(
                (THUMB_MAX_LONG_EDGE, THUMB_MAX_LONG_EDGE),
                resample=resample_filter,  # type: ignore[arg-type] # Pillow stub-version drift (see resolve above)
            )
            # JPEG can't carry alpha; flatten onto white so PNG/WebP
            # uploads with transparency don't render as black blobs.
            if im.mode not in ("RGB", "L"):
                background = Image.new("RGB", im.size, (255, 255, 255))
                if im.mode == "RGBA":
                    background.paste(im, mask=im.split()[-1])
                else:
                    background.paste(
                        im.convert("RGBA"), mask=im.convert("RGBA").split()[-1]
                    )
                im = background  # type: ignore[assignment]
            buf = io.BytesIO()
            im.save(buf, format=THUMB_FORMAT, quality=THUMB_QUALITY, optimize=True)
            thumb_bytes = buf.getvalue()
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "thumbnail generation failed for %s (%s): %s",
            attachment.id,
            attachment.mime_type,
            e,
        )
        return None

    sibling_key = svc.build_sibling_key(entry_id, attachment.id, THUMB_FILENAME)
    result = await svc._storage.save_file(  # noqa: SLF001 - intentional facade reach-in
        sibling_key,
        thumb_bytes,
        metadata={
            "entry_id": entry_id,
            "attachment_id": attachment.id,
            "kind": "thumbnail",
            "content_type": "image/jpeg",
        },
    )
    persisted_key = str(result.get("path") or sibling_key)
    attachment.thumb_storage_key = persisted_key
    return persisted_key
