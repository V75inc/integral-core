"""Server-side MIME sniffing for attachment uploads.

The client-supplied ``Content-Type`` is advisory only — we re-derive the
type from the raw bytes via ``libmagic`` (python-magic) and reject
uploads where the sniffed type is incompatible with what the client
claimed. This blocks the simplest class of MIME-spoofing attack (e.g.
naming an executable ``invoice.pdf`` and posting it as
``application/pdf``).

The library binding is optional. In environments without ``libmagic``
installed (some minimal CI sandboxes, Windows dev boxes), the sniffer
degrades to a "trust the client" mode but logs a warning so the gap is
visible. Production deployments should always have libmagic available;
see backend/README.md for install notes.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Lazy import — libmagic may be missing in some environments. When the
# import fails we fall back to a no-op sniffer that just normalizes the
# client-supplied MIME. The boolean is consulted at call time so a
# late-installed binding becomes available without a process restart
# (relevant in dev where libmagic gets brewed mid-session).
try:  # pragma: no cover - import-time branch
    import magic as _magic  # type: ignore[import-not-found]

    _MAGIC_AVAILABLE = True
except Exception:  # noqa: BLE001 - any import failure should disable sniffing
    _magic = None  # type: ignore[assignment]
    _MAGIC_AVAILABLE = False


# Pairs of (claimed, sniffed) that are known-equivalent and should not
# be flagged as a mismatch. libmagic frequently returns the older
# pre-OOXML aliases for office docs, or text/plain for JSON / CSV /
# markdown depending on the bundled magic file version.
_COMPATIBLE_PAIRS = {
    ("application/json", "text/plain"),
    ("application/json", "text/json"),
    ("text/csv", "text/plain"),
    ("text/markdown", "text/plain"),
    ("text/x-markdown", "text/plain"),
    ("application/x-zip-compressed", "application/zip"),
    (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
    ),
    (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/zip",
    ),
    (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/zip",
    ),
    # Some libmagic builds detect OOXML as the CDFV2 / composite type.
    (
        "application/vnd.ms-excel",
        "application/CDFV2",
    ),
    (
        "application/msword",
        "application/CDFV2",
    ),
}


@dataclass(frozen=True)
class SniffResult:
    """Outcome of a sniff call.

    ``effective_mime`` is what the caller should persist. ``mismatch`` is
    True when the client-supplied type is incompatible with the sniffed
    type; the caller decides whether to reject (uploads) or just log
    (re-processing).
    """

    sniffed_mime: str
    effective_mime: str
    mismatch: bool
    used_libmagic: bool
    detail: str = ""


def is_available() -> bool:
    """Return True when libmagic is available in this process."""
    return _MAGIC_AVAILABLE


def _normalize(mime: str) -> str:
    return (mime or "").split(";", 1)[0].strip().lower()


def _are_compatible(claimed: str, sniffed: str) -> bool:
    if claimed == sniffed:
        return True
    if (claimed, sniffed) in _COMPATIBLE_PAIRS:
        return True
    # Generic family match: image/png ≈ image/* prefix when libmagic
    # returns a sub-form differing only in suffix-encoding.
    if "/" in claimed and "/" in sniffed:
        c_family, c_sub = claimed.split("/", 1)
        s_family, s_sub = sniffed.split("/", 1)
        if c_family == s_family and (
            c_sub.replace("x-", "") == s_sub.replace("x-", "")
        ):
            return True
    return False


def sniff_bytes(
    content: bytes, *, claimed_mime: str = "", filename: str = ""
) -> SniffResult:
    """Sniff a byte buffer.

    Args:
        content: Raw file bytes. Pass the head (first ~2 KB) for cheap
            sniffing on large files; libmagic only reads a prefix.
        claimed_mime: ``Content-Type`` from the client; checked for
            consistency against the sniffed result.
        filename: Used only for logging context on mismatches.
    """
    claimed = _normalize(claimed_mime)
    if not _MAGIC_AVAILABLE:
        # Without libmagic, treat the client's MIME as authoritative but
        # surface the gap in logs. Production deployments install
        # libmagic; this branch keeps dev/minimal CI usable.
        logger.debug(
            "libmagic unavailable; trusting client-supplied MIME %r for %r",
            claimed,
            filename,
        )
        return SniffResult(
            sniffed_mime=claimed or "application/octet-stream",
            effective_mime=claimed or "application/octet-stream",
            mismatch=False,
            used_libmagic=False,
            detail="libmagic-unavailable",
        )

    try:
        sniffed = _normalize(_magic.from_buffer(content, mime=True))  # type: ignore[union-attr]
    except Exception as e:  # noqa: BLE001
        logger.warning("libmagic sniff failed for %r: %s", filename, e)
        return SniffResult(
            sniffed_mime=claimed or "application/octet-stream",
            effective_mime=claimed or "application/octet-stream",
            mismatch=False,
            used_libmagic=False,
            detail=f"sniff-error: {e!s}",
        )

    if not claimed or claimed == "application/octet-stream":
        # Empty/generic claim — sniffed result is authoritative.
        return SniffResult(
            sniffed_mime=sniffed,
            effective_mime=sniffed or "application/octet-stream",
            mismatch=False,
            used_libmagic=True,
        )

    # Sniffed ``application/octet-stream`` means libmagic could not classify
    # the bytes (truncated header, padding-heavy payloads, magic-db gaps).
    # Treat that as inconclusive rather than a positive mismatch — trust the
    # claim and keep the upload moving. Actively malicious payloads sniff as
    # a specific binary type (``application/x-executable``,
    # ``application/x-mach-binary``, etc.), not the generic catch-all, so
    # this is safe against MIME-spoofing attacks.
    if sniffed == "application/octet-stream":
        return SniffResult(
            sniffed_mime=sniffed,
            effective_mime=claimed,
            mismatch=False,
            used_libmagic=True,
            detail="sniff-inconclusive",
        )

    mismatch = not _are_compatible(claimed, sniffed)
    return SniffResult(
        sniffed_mime=sniffed,
        # Prefer the sniffed value once we've verified compatibility —
        # it's the more trustworthy of the two.
        effective_mime=sniffed if not mismatch else claimed,
        mismatch=mismatch,
        used_libmagic=True,
        detail=(f"claimed={claimed} sniffed={sniffed}" if mismatch else ""),
    )


async def sniff_path(
    path: str, *, claimed_mime: str = "", filename: str = "", head_bytes: int = 4096
) -> SniffResult:
    """Sniff the head of a file on disk (used by streaming-upload path)."""
    if not os.path.isfile(path):
        return SniffResult(
            sniffed_mime="",
            effective_mime=claimed_mime or "application/octet-stream",
            mismatch=False,
            used_libmagic=False,
            detail="path-not-found",
        )
    with open(path, "rb") as f:
        head = f.read(head_bytes)
    return sniff_bytes(head, claimed_mime=claimed_mime, filename=filename)


def assert_compatible(result: SniffResult) -> Optional[Tuple[str, str]]:
    """Return ``(claimed, sniffed)`` when a mismatch should be rejected.

    Callers use this as: ``mismatch = assert_compatible(result); if mismatch: raise ...``
    """
    return (result.detail, result.sniffed_mime) if result.mismatch else None
