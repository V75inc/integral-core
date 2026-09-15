"""Tests for the Pillow-based avatar resize pipeline.

Phase 9 Plan 09-01 (AVT-01). The pipeline lives at
``app/services/avatar_resize.py`` and is invoked by
``POST /api/users/{user_id}/avatar`` after the 5MB hard cap has gated
the request (A4 — Pillow must never see oversize bytes).
"""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from app.schemas.avatar import AvatarValidationError
from app.services.avatar_resize import resize_avatar


def _png_bytes(width: int, height: int, color=(120, 64, 200, 255)) -> bytes:
    img = Image.new("RGBA", (width, height), color=color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_resize_square_input_produces_all_sizes():
    """Test 1: a 1024x1024 PNG resizes to all four canonical variants."""
    src = _png_bytes(1024, 1024)
    out = resize_avatar(src)
    assert set(out.keys()) == {32, 64, 128, 256}
    for size, png in out.items():
        loaded = Image.open(BytesIO(png))
        assert loaded.format == "PNG"
        assert loaded.size == (size, size)


def test_resize_non_square_center_crops_first():
    """Test 2: a non-square input is center-cropped before resize."""
    # 800x400 horizontal — should crop to 400x400, then resize.
    src = _png_bytes(800, 400)
    out = resize_avatar(src, sizes=(256,))
    loaded = Image.open(BytesIO(out[256]))
    assert loaded.size == (256, 256)


def test_resize_corrupted_bytes_raises():
    """Test 3: a corrupted byte string raises AvatarValidationError."""
    with pytest.raises(AvatarValidationError):
        resize_avatar(b"not-a-real-image-just-garbage")
