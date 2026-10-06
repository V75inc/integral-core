"""Visual theme for rendered documents.

A theme is a small set of colours and fonts. The defaults give a clean, modern
business look (navy accent, soft shaded tables and callouts). A template can
override them with ``accent_color``, ``heading_font`` and ``body_font``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")
_FONT = re.compile(r"^[A-Za-z0-9 \-_.]{1,40}$")


def _mix(hex_color: str, with_hex: str, amount: float) -> str:
    """Blend ``hex_color`` toward ``with_hex`` by ``amount`` (0-1), as RRGGBB."""
    a = [int(hex_color[i : i + 2], 16) for i in (0, 2, 4)]
    b = [int(with_hex[i : i + 2], 16) for i in (0, 2, 4)]
    return "".join(f"{round(x + (y - x) * amount):02X}" for x, y in zip(a, b))


@dataclass(frozen=True)
class Theme:
    accent: str = "1F4E79"
    heading_font: str = "Calibri"
    body_font: str = "Calibri"
    text: str = "262626"
    muted: str = "6B7280"
    rule: str = "D0D7DE"

    @property
    def accent_mid(self) -> str:
        """Second-level heading colour: the accent, a little lighter."""
        return _mix(self.accent, "FFFFFF", 0.18)

    @property
    def accent_soft(self) -> str:
        """Very light tint of the accent (callout and band backgrounds)."""
        return _mix(self.accent, "FFFFFF", 0.92)

    @property
    def band(self) -> str:
        return _mix(self.accent, "FFFFFF", 0.95)

    @property
    def code_bg(self) -> str:
        return "F3F4F6"


def make_theme(raw: Optional[Dict[str, Any]] = None) -> Theme:
    """Theme from template settings; anything invalid falls back to the default."""
    raw = raw or {}
    base = Theme()
    accent = base.accent
    m = _HEX.match(str(raw.get("accent_color") or raw.get("accent") or "").strip())
    if m:
        accent = m.group(1).upper()

    def font(key_a: str, key_b: str, default: str) -> str:
        value = str(raw.get(key_a) or raw.get(key_b) or "").strip()
        return value if _FONT.match(value) else default

    return Theme(
        accent=accent,
        heading_font=font("heading_font", "font_heading", base.heading_font),
        body_font=font("body_font", "font_body", base.body_font),
    )
