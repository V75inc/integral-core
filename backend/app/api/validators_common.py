"""Shared validation helpers used by Pydantic schemas and service layer.

Single source of truth for the regexes and pure helper functions referenced
by entity validation logic across the backend. Schemas import these to keep
format checks consistent; services import :func:`compute_fold` to denormalize
case- and whitespace-insensitive lookup keys at the persistence boundary.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

from app.exceptions import BadRequestError

_HEX3_COLOR = re.compile(r"^#[0-9a-fA-F]{3}$")
_HEX6_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_SEMVER_ISH = re.compile(r"^\d+(\.\d+){1,2}([-+][A-Za-z0-9.\-]+)?$")
_SLUG_KEY = re.compile(r"^[a-z][a-z0-9_]*$")


def compute_fold(value: Optional[str]) -> str:
    """Canonicalize a name/title for case-, whitespace- and accent-insensitive
    comparison.

    Accent folding is not cosmetic here. Until jvspatial text normalization was
    turned off (JVSPATIAL_TEXT_NORMALIZATION_ENABLED), the storage layer
    ASCII-folded every persisted string, so a workspace named "Café" was stored
    as "Cafe" and this function -- plain casefold -- produced "cafe". With
    normalization off the same input now persists as "Café" and would fold to
    "café", which does NOT match the "cafe" already stored on every row written
    before the change. Uniqueness keys off these columns, so the two would stop
    colliding and a duplicate could be created that looks identical in the UI.

    Folding here rather than relying on the storage layer keeps the comparison
    key stable across that change: display text keeps its accents, the match key
    never had any. Rows written between the flag flip and this fix carry an
    accented fold; ``scripts/backfill_fold_columns.py`` recomputes them.
    """
    if value is None:
        return ""
    stripped = unicodedata.normalize("NFKD", str(value).strip())
    without_marks = "".join(c for c in stripped if not unicodedata.combining(c))
    return without_marks.casefold()


def non_empty_after_strip(value: Optional[str], field: str) -> str:
    """Return the stripped value or raise if it would be empty."""
    if value is None:
        raise BadRequestError(message=f"{field} is required")
    stripped = str(value).strip()
    if not stripped:
        raise BadRequestError(message=f"{field} must not be empty")
    return stripped


def validate_hex_color(value: Optional[str], *, allow_empty: bool = True) -> str:
    """Validate a hex color string (#RGB or #RRGGBB). Returns the lowercased #RRGGBB form."""
    if value is None:
        if allow_empty:
            return ""
        raise BadRequestError(message="color must be a #RGB or #RRGGBB hex string")
    s = str(value).strip()
    if not s:
        if allow_empty:
            return ""
        raise BadRequestError(message="color must be a #RGB or #RRGGBB hex string")
    if _HEX3_COLOR.match(s):
        r, g, b = s[1], s[2], s[3]
        return f"#{r}{r}{g}{g}{b}{b}".lower()
    if _HEX6_COLOR.match(s):
        return s.lower()
    raise BadRequestError(message="color must be a #RGB or #RRGGBB hex string")


def validate_email(value: Optional[str]) -> str:
    """Validate and normalize an email address (casefold)."""
    if value is None:
        raise BadRequestError(message="email is required")
    s = str(value).strip()
    if not s or not _EMAIL.match(s):
        raise BadRequestError(message="email must be a valid email address")
    return s.casefold()


def validate_semver_ish(value: Optional[str], *, allow_empty: bool = True) -> str:
    """Validate a relaxed semver string (N.N or N.N.N with optional pre-release/build)."""
    if value is None:
        if allow_empty:
            return ""
        raise BadRequestError(message="version must be a semver-like string")
    s = str(value).strip()
    if not s:
        if allow_empty:
            return ""
        raise BadRequestError(message="version must be a semver-like string")
    if not _SEMVER_ISH.match(s):
        raise BadRequestError(
            message="version must look like '1', '1.0', or '1.0.0' (optional -prerelease)",
        )
    return s


def validate_slug_key(value: Optional[str], *, field: str = "key") -> str:
    """Validate a manifest-internal key: lowercase ascii, digits, underscores; must start with a letter."""
    if value is None:
        raise BadRequestError(message=f"{field} is required")
    s = str(value).strip()
    if not s or not _SLUG_KEY.match(s):
        raise BadRequestError(
            message=f"{field} must match [a-z][a-z0-9_]* (lowercase letters, digits, underscores)",
        )
    return s
