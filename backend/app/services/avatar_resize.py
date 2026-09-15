"""Pillow-based square-crop + multi-size PNG resize for User avatars.

Phase 9 Plan 09-01 (AVT-01). All outputs are PNG regardless of input
MIME — keeps the GET surface single-content-type. Square-crop is
center-anchored. ``Image.verify()`` runs BEFORE resize to bound the
CPU cost of a malformed payload (A4 — 5 MB hard cap upstream).

Threat hardening (STRIDE T-09-01-T01 / T-09-01-D01):
    - ``Image.verify()`` rejects corrupted PNG/JPEG/WebP byte streams
      before the resize-and-encode loop runs.
    - The upstream ``api/users.py`` validates ``len(content) <= 5_000_000``
      before this function is invoked, so Pillow only sees bounded inputs.
    - Pillow's default ``MAX_IMAGE_PIXELS`` (~89.5M pixels) caps the
      decompressed dimension product, defending against decompression-bomb
      PNGs that are small on the wire but expand to huge in-memory images.
"""

from __future__ import annotations

from io import BytesIO
from typing import Dict, Iterable

from PIL import Image, UnidentifiedImageError

from app.schemas.avatar import AvatarValidationError

_DEFAULT_SIZES: tuple = (32, 64, 128, 256)


def resize_avatar(
    image_bytes: bytes,
    sizes: Iterable[int] = _DEFAULT_SIZES,
) -> Dict[int, bytes]:
    """Center-crop to square, resize to each requested size, return PNG bytes.

    Args:
        image_bytes: Raw upload bytes — must be a PNG, JPEG, or WebP image
            payload <= ``AVATAR_MAX_UPLOAD_BYTES`` (validated upstream).
        sizes: Iterable of pixel sizes to produce. Each size N yields one
            NxN PNG variant. Defaults to (32, 64, 128, 256).

    Returns:
        ``Dict[size_int, png_bytes]`` — one entry per requested size.

    Raises:
        AvatarValidationError: when the payload fails ``Image.verify()``
            or cannot be decoded by Pillow.
    """
    try:
        # ``verify()`` consumes the stream — must reopen for processing.
        Image.open(BytesIO(image_bytes)).verify()
        img = Image.open(BytesIO(image_bytes)).convert("RGBA")
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
    ) as exc:
        raise AvatarValidationError(f"Invalid image payload: {exc}") from exc

    # Center-crop to square so the resize step never distorts aspect ratio.
    w, h = img.size
    if w != h:
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        img = img.crop((left, top, left + side, top + side))

    out: Dict[int, bytes] = {}
    for size in sizes:
        buf = BytesIO()
        # LANCZOS gives the best quality for downscale at a modest CPU cost
        # (~10ms per resize at the 256→256 ceiling).
        img.resize((size, size), Image.LANCZOS).save(buf, format="PNG")  # type: ignore[attr-defined]
        out[size] = buf.getvalue()
    return out
