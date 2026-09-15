"""Profanity detection for public-facing user content.

Guards free-text write paths reachable without authentication (public
share-link entries/comments) and the equivalent authenticated entry/comment
endpoints, per the June 23 QA feature request: detect + block profane
language in comments, entry creation, and entry editing before it persists.

Deliberately minimal — a single ``validate_no_profanity`` call per free-text
field, raising ``BadRequestError`` on a match. No wordlist customization or
flag-instead-of-block mode yet; that's a documented follow-up, not scope
creep for this pass.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Optional, Protocol

from app.api.errors import BadRequestError

logger = logging.getLogger(__name__)

# Minimal safety net when ``better_profanity`` is missing or is the divergent
# PyPI 1.x package (wordlist omits common conjugations like "fucking").
# Keep this short — the 0.7.x library is the primary filter.
_FALLBACK_PROFANE = frozenset(
    {
        "fuck",
        "fucking",
        "fucked",
        "fucker",
        "shit",
        "shitty",
        "asshole",
        "bitch",
        "bastard",
        "cunt",
        "dick",
        "piss",
        "motherfucker",
    }
)
_FALLBACK_TOKEN_RE = re.compile(r"[a-z0-9']+", re.IGNORECASE)


class _ProfanityFilter(Protocol):
    def contains_profanity(self, text: str) -> bool: ...


class _FallbackFilter:
    """Token-set matcher used when the preferred library is unavailable."""

    def contains_profanity(self, text: str) -> bool:
        if not text:
            return False
        return any(
            tok.lower() in _FALLBACK_PROFANE for tok in _FALLBACK_TOKEN_RE.findall(text)
        )


def _package_major_version() -> Optional[int]:
    try:
        from importlib.metadata import version as _pkg_version

        ver = _pkg_version("better-profanity")
    except Exception:  # noqa: BLE001 — best-effort version probe
        return None
    try:
        return int(str(ver).split(".", 1)[0] or "0")
    except ValueError:
        return None


@lru_cache(maxsize=1)
def _filter() -> _ProfanityFilter:
    """Lazily construct + cache the compiled wordlist (import is not free).

    Prefers ``better_profanity`` 0.7.x. Falls back to a small built-in token
    set when the package is missing or is the divergent PyPI 1.x release —
    moderation must not silently disable itself under an open ``>=0.7.0`` pin
    that resolves to 1.0.
    """
    try:
        from better_profanity import profanity
    except ImportError:
        logger.warning(
            "content_moderation: 'better_profanity' not installed — using "
            "built-in fallback wordlist. Install better-profanity>=0.7.0,<1 "
            "for the full filter (see backend/pyproject.toml)."
        )
        return _FallbackFilter()

    major = _package_major_version()
    if major is not None and major >= 1:
        logger.error(
            "content_moderation: better-profanity %s is unsupported (need "
            "0.7.x). Using built-in fallback wordlist. Pin "
            "better-profanity>=0.7.0,<1 and reinstall.",
            major,
        )
        return _FallbackFilter()

    profanity.load_censor_words()
    return profanity


def contains_profanity(text: str) -> bool:
    """True if ``text`` contains flagged language. Empty/None text is clean.

    When ``_filter()`` returns ``None`` (test monkeypatch / explicit disable),
    fails open so free-text writes are never hard-blocked by a missing filter.
    """
    if not text:
        return False
    flt = _filter()
    if flt is None:
        return False
    return bool(flt.contains_profanity(text))


def validate_no_profanity(text: str, field_name: str) -> None:
    """Raise ``BadRequestError`` if ``text`` contains flagged language.

    Call before persisting any public-facing free-text field (entry title/body,
    comment text). No-op on empty text — required-ness is validated elsewhere.
    """
    if contains_profanity(text):
        raise BadRequestError(
            message=(
                f"The '{field_name}' field contains language that isn't allowed "
                "here. Please revise and try again."
            )
        )
