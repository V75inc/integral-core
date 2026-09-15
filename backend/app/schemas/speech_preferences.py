"""Wire shape for ``User.speech_preferences`` — per-user dictation settings.

Stored as a plain dict on the ``User`` node (the ``notification_preferences``
idiom); this Pydantic boundary enforces the shape. Kept out of the free-form
``User.preferences`` dict so the profile endpoints can't write it past
validation.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: ``auto`` — the workspace's provider when one is available to the user,
#: otherwise the browser's recognizer. ``browser`` — always the browser's
#: (a privacy or cost preference).
SpeechEngineChoice = Literal["auto", "browser"]
HotkeyMode = Literal["hold_or_toggle", "toggle", "hold"]

_LANGUAGE_TAG = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_HOTKEY_MODIFIERS = frozenset({"Mod", "Ctrl", "Alt", "Shift", "Meta"})
_HOTKEY_KEY = re.compile(
    r"^(?:Space|Enter|Period|Comma|Slash|Semicolon|Quote|Backquote|Minus|Equal"
    r"|[A-Z0-9]|F(?:[1-9]|1[0-2]))$"
)


def validate_language_tag(value: str) -> str:
    """Accept ``auto`` or a BCP-47-shaped tag (``en``, ``en-US``, ``pt-BR``)."""
    tag = (value or "").strip()
    if tag == "auto" or _LANGUAGE_TAG.match(tag):
        return tag
    raise ValueError(
        f"language must be 'auto' or a BCP-47 tag like 'en-US', got {value!r}"
    )


def validate_hotkey(value: str) -> str:
    """Accept ``Mod+Shift+Space``-style chords with at least one modifier.

    ``Mod`` is ⌘ on macOS and Ctrl elsewhere. A bare key is refused because
    it would fire while the user types into the composer.
    """
    parts = [p.strip() for p in (value or "").split("+")]
    *modifiers, key = parts
    if not modifiers:
        raise ValueError("hotkey needs at least one modifier, e.g. 'Mod+Shift+Space'")
    if len(set(modifiers)) != len(modifiers) or not set(modifiers) <= _HOTKEY_MODIFIERS:
        raise ValueError(
            f"hotkey modifiers must be distinct and from {sorted(_HOTKEY_MODIFIERS)}"
        )
    if not _HOTKEY_KEY.match(key):
        raise ValueError(f"unsupported hotkey key {key!r}")
    return "+".join(parts)


class SpeechPreferences(BaseModel):
    """One user's dictation settings; every field has a working default."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    engine: SpeechEngineChoice = "auto"
    language: str = Field(default="auto", max_length=35)
    hotkey: str = Field(default="Mod+Shift+Space", max_length=40)
    hotkey_mode: HotkeyMode = "hold_or_toggle"
    auto_send_on_stop: bool = False
    #: Stop listening after this many seconds without speech; 0 disables.
    silence_timeout_seconds: int = Field(default=8, ge=0, le=60)

    @field_validator("language")
    @classmethod
    def _language(cls, value: str) -> str:
        return validate_language_tag(value)

    @field_validator("hotkey")
    @classmethod
    def _hotkey(cls, value: str) -> str:
        return validate_hotkey(value)
