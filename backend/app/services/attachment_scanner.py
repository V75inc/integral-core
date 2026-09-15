"""Pluggable malware / safety scanner interface for attachment uploads.

The framework is wired into the upload pipeline but ships with a no-op
implementation by default. The intent is:

    1. New uploads get scanned post-write, pre-publish.
    2. The scanner sets ``Attachment.scan_status`` to one of
       ``clean | blocked | failed | skipped``.
    3. Attachments where ``scan_status == "blocked"`` are filtered out
       of list/get/download endpoints (poisoned content stays in the
       graph for audit but is unreachable through user-facing routes).

Concrete scanners (ClamAV, cloud-vendor scanning APIs, hash blocklists,
etc.) register themselves under a string name and are selected via the
``ATTACHMENT_SCANNER`` setting. A scanner can return either a synchronous
verdict or — for slow engines — opt into the deferred queue that the
metadata extractor already provides (future Phase 5 work).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)

# Status values match Attachment.scan_status. Keep this list aligned
# with the model docstring so callers don't drift.
SCAN_STATUS_PENDING = "pending"
SCAN_STATUS_CLEAN = "clean"
SCAN_STATUS_BLOCKED = "blocked"
SCAN_STATUS_FAILED = "failed"
SCAN_STATUS_SKIPPED = "skipped"


@dataclass(frozen=True)
class ScanResult:
    """Verdict from a scanner run."""

    status: str  # one of SCAN_STATUS_*
    engine: str
    message: str = ""

    @property
    def is_blocked(self) -> bool:
        return self.status == SCAN_STATUS_BLOCKED


class AttachmentScanner(ABC):
    """Abstract scanner. Implementations are sync-or-async safe."""

    name: str = "abstract"

    @abstractmethod
    async def scan(
        self, *, content: bytes, mime_type: str, filename: str
    ) -> ScanResult:
        """Inspect ``content`` and return a ScanResult."""
        raise NotImplementedError


class NoopScanner(AttachmentScanner):
    """Default scanner — marks every upload as ``skipped``.

    Deliberately not ``clean`` so production audits can detect that no
    real engine was wired.
    """

    name = "noop"

    async def scan(
        self, *, content: bytes, mime_type: str, filename: str
    ) -> ScanResult:
        return ScanResult(
            status=SCAN_STATUS_SKIPPED,
            engine=self.name,
            message="No scanner configured (ATTACHMENT_SCANNER=noop).",
        )


_REGISTRY: Dict[str, Callable[[], AttachmentScanner]] = {
    "noop": NoopScanner,
}


def register_scanner(name: str, factory: Callable[[], AttachmentScanner]) -> None:
    """Register a scanner factory under ``name``.

    Idempotent; later calls overwrite earlier registrations. Real
    scanners should be registered during app startup so the
    ``ATTACHMENT_SCANNER`` setting can resolve them.
    """
    _REGISTRY[name] = factory


_DEFAULT_SCANNER: Optional[AttachmentScanner] = None


def get_attachment_scanner() -> AttachmentScanner:
    """Return the process-wide scanner selected by the config."""
    global _DEFAULT_SCANNER
    if _DEFAULT_SCANNER is not None:
        return _DEFAULT_SCANNER
    # Imported lazily to avoid a config <-> services circular dep at
    # module-import time.
    from app.config import settings

    selected = (settings.ATTACHMENT_SCANNER or "noop").strip().lower()
    factory = _REGISTRY.get(selected) or _REGISTRY["noop"]
    if selected not in _REGISTRY:
        logger.warning(
            "ATTACHMENT_SCANNER=%r not registered; falling back to NoopScanner.",
            selected,
        )
    _DEFAULT_SCANNER = factory()
    return _DEFAULT_SCANNER


def reset_for_testing() -> None:
    """Drop the cached scanner singleton (used by test fixtures)."""
    global _DEFAULT_SCANNER
    _DEFAULT_SCANNER = None
