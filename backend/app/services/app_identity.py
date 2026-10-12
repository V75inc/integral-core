"""Bounded presentation-only App icons without remote image requests."""

import base64
import re
from typing import Optional

from app.api.errors import BadRequestError

APP_ICON_KEYS = frozenset(
    {"layers", "briefcase", "book", "chart", "calendar", "globe", "sparkles", "leaf"}
)
MAX_LOGO_BYTES = 128 * 1024


def validate_app_icon(value: Optional[str]) -> str:
    """Accept a built-in icon key or a small inline PNG, JPEG or WebP logo."""
    icon = (value or "").strip()
    if not icon or icon in APP_ICON_KEYS:
        return icon
    match = re.fullmatch(r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)", icon)
    if match and len(icon) <= MAX_LOGO_BYTES * 4 // 3 + 100:
        try:
            raw = base64.b64decode(match[2], validate=True)
        except ValueError:
            raw = b""
        signatures = {
            "png": raw.startswith(b"\x89PNG\r\n\x1a\n"),
            "jpeg": raw.startswith(b"\xff\xd8\xff"),
            "webp": raw.startswith(b"RIFF") and raw[8:12] == b"WEBP",
        }
        if raw and len(raw) <= MAX_LOGO_BYTES and signatures[match[1]]:
            return icon
    raise BadRequestError(
        message="Choose a built-in icon or a PNG, JPEG or WebP logo up to 128 KB"
    )
