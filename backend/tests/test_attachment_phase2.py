"""Tests for Plan 03 — Phase 2 (storage & delivery polish).

Covers:
    - AttachmentStorageService.get_signed_url duck-types correctly:
      returns the provider's URL when it supports presigning, returns
      None otherwise.
    - Image thumbnail generation: ensure_thumbnail produces a sibling
      JPEG, sets thumb_storage_key, is a no-op for non-images.
    - LibreOffice preview service: can_preview MIME table, missing
      binary raises PreviewUnavailableError, successful conversion
      caches the PDF and idempotently reuses it on the second call.
"""

from __future__ import annotations

import io
import os
from datetime import datetime
from typing import Optional

import pytest

from app.models.nodes import Attachment
from app.services import attachment_preview
from app.services.attachment_storage import (
    AttachmentStorageService,
    get_attachment_storage_service,
)
from app.services.attachment_thumbnails import ensure_thumbnail

# ---------------------------------------------------------------------------
# Signed URL — duck-typed against a stub storage
# ---------------------------------------------------------------------------


class _StubStorageWithPresign:
    """Mimics a jvspatial S3 storage backend that exposes presigning."""

    async def generate_presigned_url(
        self,
        key: str,
        *,
        expires_in: int = 300,
        download_filename: Optional[str] = None,
    ) -> str:
        # Echo back a deterministic, inspectable URL.
        suffix = f"?fn={download_filename}" if download_filename else ""
        return f"https://signed.example/{key}?ttl={expires_in}{suffix}"


class _StubStorageNoPresign:
    """Mimics a local-filesystem provider with no presigning method."""

    # Intentionally empty — get_signed_url should return None.


@pytest.mark.asyncio
async def test_signed_url_uses_provider_method_when_available():
    svc = AttachmentStorageService(storage=_StubStorageWithPresign())  # type: ignore[arg-type]
    url = await svc.get_signed_url(
        "attachments/e/a/x.pdf", expires_in=600, download_filename="x.pdf"
    )
    assert url == "https://signed.example/attachments/e/a/x.pdf?ttl=600?fn=x.pdf"


@pytest.mark.asyncio
async def test_signed_url_returns_none_when_provider_lacks_method():
    svc = AttachmentStorageService(storage=_StubStorageNoPresign())  # type: ignore[arg-type]
    url = await svc.get_signed_url("attachments/e/a/x.pdf")
    assert url is None


@pytest.mark.asyncio
async def test_signed_url_returns_none_for_empty_key():
    svc = AttachmentStorageService(storage=_StubStorageWithPresign())  # type: ignore[arg-type]
    assert await svc.get_signed_url("") is None


# ---------------------------------------------------------------------------
# Image thumbnails
# ---------------------------------------------------------------------------


def _pillow_available() -> bool:
    try:
        import PIL.Image  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def _png_bytes(size: tuple[int, int] = (1024, 768)) -> bytes:
    """Synthesise a small PNG so the test doesn't need a fixture file."""
    from PIL import Image

    im = Image.new("RGB", size, (10, 120, 200))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
@pytest.mark.skipif(not _pillow_available(), reason="Pillow not installed")
async def test_ensure_thumbnail_produces_sibling_jpeg():
    storage = get_attachment_storage_service()
    png = _png_bytes((1024, 768))
    att = await Attachment.create(
        filename="hero.png",
        mime_type="image/png",
        size=len(png),
        storage_key="",
        uploaded_by="u1",
        scan_status="clean",
        created_at=datetime.now().isoformat(),
    )
    stored = await storage.save_attachment(
        entry_id="entry-thumb",
        attachment_id=att.id,
        filename="hero.png",
        content=png,
    )
    att.storage_key = str(stored.get("path") or "")
    await att.save()

    thumb_key = await ensure_thumbnail(
        att, entry_id="entry-thumb", content=png, storage=storage
    )
    assert thumb_key is not None
    assert thumb_key.endswith("_thumb.jpg")
    assert att.thumb_storage_key == thumb_key

    # The persisted thumb is a real, downscaled JPEG.
    thumb_bytes = await storage.read_attachment(thumb_key)
    assert thumb_bytes is not None
    assert thumb_bytes[:3] == b"\xff\xd8\xff"  # JPEG SOI marker

    # Reload through Pillow to confirm dimensions are capped.
    from PIL import Image

    with Image.open(io.BytesIO(thumb_bytes)) as im:
        assert max(im.size) <= 512


@pytest.mark.asyncio
async def test_ensure_thumbnail_is_noop_for_non_images():
    storage = get_attachment_storage_service()
    txt = b"hello"
    att = await Attachment.create(
        filename="note.txt",
        mime_type="text/plain",
        size=len(txt),
        storage_key="",
        uploaded_by="u1",
        scan_status="clean",
        created_at=datetime.now().isoformat(),
    )
    stored = await storage.save_attachment(
        entry_id="entry-no-thumb",
        attachment_id=att.id,
        filename="note.txt",
        content=txt,
    )
    att.storage_key = str(stored.get("path") or "")
    await att.save()

    result = await ensure_thumbnail(
        att, entry_id="entry-no-thumb", content=txt, storage=storage
    )
    assert result is None
    assert att.thumb_storage_key == ""


# ---------------------------------------------------------------------------
# LibreOffice preview service
# ---------------------------------------------------------------------------


def test_can_preview_mime_table():
    assert attachment_preview.can_preview(
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )
    assert attachment_preview.can_preview("application/vnd.ms-powerpoint")
    assert attachment_preview.can_preview("application/msword")
    assert attachment_preview.can_preview("application/rtf")
    # PDFs are their own preview — not in the table.
    assert not attachment_preview.can_preview("application/pdf")
    # Images render natively in the browser.
    assert not attachment_preview.can_preview("image/png")
    assert not attachment_preview.can_preview("")


@pytest.mark.asyncio
async def test_ensure_preview_returns_none_for_non_previewable_type():
    att = await Attachment.create(
        filename="x.png",
        mime_type="image/png",
        size=10,
        storage_key="some/key",
        uploaded_by="u1",
        scan_status="clean",
        created_at=datetime.now().isoformat(),
    )
    result = await attachment_preview.ensure_preview(att, entry_id="e1")
    assert result is None


@pytest.mark.asyncio
async def test_ensure_preview_raises_when_libreoffice_missing(monkeypatch):
    """When the binary is absent, the service raises a clear error so
    the HTTP endpoint can map it to 503."""
    monkeypatch.setattr(attachment_preview, "_resolve_libreoffice_binary", lambda: None)

    storage = get_attachment_storage_service()
    fake_pptx = b"PK\x03\x04" + b"\x00" * 256  # ZIP magic; libreoffice would parse it
    att = await Attachment.create(
        filename="deck.pptx",
        mime_type=(
            "application/vnd.openxmlformats-officedocument."
            "presentationml.presentation"
        ),
        size=len(fake_pptx),
        storage_key="",
        uploaded_by="u1",
        scan_status="clean",
        created_at=datetime.now().isoformat(),
    )
    stored = await storage.save_attachment(
        entry_id="entry-preview",
        attachment_id=att.id,
        filename="deck.pptx",
        content=fake_pptx,
    )
    att.storage_key = str(stored.get("path") or "")
    await att.save()

    with pytest.raises(attachment_preview.PreviewUnavailableError):
        await attachment_preview.ensure_preview(att, entry_id="entry-preview")


@pytest.mark.asyncio
async def test_ensure_preview_caches_then_reuses(monkeypatch, tmp_path):
    """A successful conversion caches the PDF and a second call
    short-circuits without rerunning LibreOffice."""

    call_count = {"n": 0}

    async def fake_convert(binary, content, *, suffix):
        call_count["n"] += 1
        return b"%PDF-1.4\n" + b"\x00" * 32

    monkeypatch.setattr(
        attachment_preview, "_resolve_libreoffice_binary", lambda: "/fake/soffice"
    )
    monkeypatch.setattr(attachment_preview, "_convert_to_pdf", fake_convert)

    # libmagic on this fake payload sniffs ``application/octet-stream`` and the
    # storage validator rejects it before the test ever reaches the LibreOffice
    # preview path. Force the validator to claim the canonical PPTX MIME so
    # save_attachment + ensure_preview run end-to-end against the stubbed
    # converter. The same approach is used elsewhere in this suite when a
    # stub payload would not survive content-based MIME sniffing.
    from jvspatial.storage.security.validator import FileValidator

    monkeypatch.setattr(
        FileValidator,
        "detect_mime_type",
        lambda self, content, filename=None: (
            "application/vnd.openxmlformats-officedocument."
            "presentationml.presentation"
        ),
    )

    storage = get_attachment_storage_service()
    payload = b"PK\x03\x04" + b"\x00" * 32
    att = await Attachment.create(
        filename="cache-me.pptx",
        mime_type=(
            "application/vnd.openxmlformats-officedocument."
            "presentationml.presentation"
        ),
        size=len(payload),
        storage_key="",
        uploaded_by="u1",
        scan_status="clean",
        created_at=datetime.now().isoformat(),
    )
    stored = await storage.save_attachment(
        entry_id="entry-cache",
        attachment_id=att.id,
        filename="cache-me.pptx",
        content=payload,
    )
    att.storage_key = str(stored.get("path") or "")
    await att.save()

    key1 = await attachment_preview.ensure_preview(att, entry_id="entry-cache")
    assert key1 is not None
    assert key1.endswith("_preview.pdf")
    assert call_count["n"] == 1

    # Reload to ensure persistence stuck across instances.
    reloaded = await Attachment.get(att.id)
    assert reloaded.preview_storage_key == key1

    # Second call should hit cache.
    key2 = await attachment_preview.ensure_preview(reloaded, entry_id="entry-cache")
    assert key2 == key1
    assert call_count["n"] == 1, "cached preview should not re-invoke the converter"

    # Force=True rebuilds.
    key3 = await attachment_preview.ensure_preview(
        reloaded, entry_id="entry-cache", force=True
    )
    assert key3 == key1
    assert call_count["n"] == 2
