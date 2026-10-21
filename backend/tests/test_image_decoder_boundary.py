"""Untrusted image inputs must not reach unsupported Pillow decoders."""

from __future__ import annotations

import base64
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image, TiffImagePlugin

from app.schemas.avatar import AvatarValidationError
from app.services.attachment_thumbnails import ensure_thumbnail
from app.services.avatar_resize import resize_avatar
from app.services.documents.signature_overlay import (
    decode_signature_png,
    normalize_signature_png_alpha,
)

pytestmark = pytest.mark.unit


def image_bytes(format_name: str) -> bytes:
    out = BytesIO()
    Image.new("RGB", (16, 8), (180, 20, 30)).save(out, format=format_name)
    return out.getvalue()


@pytest.mark.parametrize("format_name", ["PNG", "JPEG", "WEBP"])
def test_avatar_accepts_its_documented_formats(format_name):
    resized = resize_avatar(image_bytes(format_name), sizes=(8,))
    with Image.open(BytesIO(resized[8])) as result:
        assert result.format == "PNG"
        assert result.size == (8, 8)


@pytest.mark.parametrize("format_name", ["TIFF", "GIF", "PPM", "BMP"])
def test_avatar_rejects_other_decoded_formats(format_name):
    with pytest.raises(AvatarValidationError):
        resize_avatar(image_bytes(format_name))


def test_signatures_require_verified_png_bytes():
    png = image_bytes("PNG")
    assert decode_signature_png(base64.b64encode(png).decode()) == png
    assert normalize_signature_png_alpha(png).startswith(b"\x89PNG\r\n\x1a\n")
    for data in [image_bytes("TIFF"), image_bytes("JPEG"), b"\x89PNG\r\n\x1a\nbroken"]:
        with pytest.raises(ValueError, match="PNG"):
            decode_signature_png(base64.b64encode(data).decode())
        with pytest.raises(ValueError, match="PNG"):
            normalize_signature_png_alpha(data)


@pytest.mark.asyncio
@pytest.mark.parametrize("mime_type", ["image/tiff", "image/png"])
async def test_thumbnail_rejects_tiff_even_with_a_spoofed_png_mime(
    monkeypatch, mime_type
):
    data = image_bytes("TIFF")

    def forbidden_decoder(*args, **kwargs):
        pytest.fail("TIFF decoder must not be dispatched")

    monkeypatch.setattr(TiffImagePlugin.TiffImageFile, "_open", forbidden_decoder)

    class Storage:
        async def read_attachment(self, key):
            pytest.fail("Provided content must not be fetched again")

        def build_sibling_key(self, *args):
            pytest.fail("Rejected content must not produce a preview")

    attachment = SimpleNamespace(
        id="synthetic",
        source_type="file",
        mime_type=mime_type,
        storage_key="original.tiff",
        thumb_storage_key="",
    )
    assert (
        await ensure_thumbnail(
            attachment, entry_id="synthetic", content=data, storage=Storage()
        )
        is None
    )
    assert attachment.storage_key == "original.tiff"
    assert attachment.thumb_storage_key == ""
    with pytest.raises(AvatarValidationError):
        resize_avatar(data)
    with pytest.raises(ValueError, match="PNG"):
        normalize_signature_png_alpha(data)


@pytest.mark.asyncio
async def test_gif_preview_uses_only_the_first_frame(monkeypatch):
    from PIL import GifImagePlugin

    out = BytesIO()
    Image.new("RGB", (16, 8), (200, 10, 10)).save(
        out,
        format="GIF",
        save_all=True,
        append_images=[Image.new("RGB", (16, 8), (10, 200, 10))],
    )
    original_seek = GifImagePlugin.GifImageFile.seek

    def first_frame_only(self, frame):
        assert frame == 0, "Thumbnailing must never seek a later GIF frame"
        return original_seek(self, frame)

    monkeypatch.setattr(GifImagePlugin.GifImageFile, "seek", first_frame_only)
    saved = []

    class Storage:
        _storage = None

        def __init__(self):
            self._storage = self

        def build_sibling_key(self, *args):
            return "_thumb.jpg"

        async def save_file(self, key, content, **kwargs):
            saved.append(content)
            return {"path": key}

    attachment = SimpleNamespace(
        id="synthetic",
        source_type="file",
        mime_type="image/gif",
        storage_key="original.gif",
        thumb_storage_key="",
    )
    assert (
        await ensure_thumbnail(
            attachment, entry_id="synthetic", content=out.getvalue(), storage=Storage()
        )
        == "_thumb.jpg"
    )
    with Image.open(BytesIO(saved[0])) as thumbnail:
        red, green, _blue = thumbnail.getpixel((0, 0))
        assert red > green
