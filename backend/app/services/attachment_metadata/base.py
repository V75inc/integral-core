"""Extractor ABC + MetadataResult shape."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class MetadataResult:
    """What an extractor returns for one attachment.

    ``common`` holds fields that apply across all MIME types (page_count
    is shared by PDFs / docx / pptx, width/height by images and videos,
    duration by audio/video). ``type_specific`` is the namespaced bag —
    PDF metadata properties, EXIF, ID3 tags, sheet names, etc.

    ``extracted_text`` is the searchable full-text projection. Empty
    string is valid — not every type has meaningful text content.

    ``partial`` is True when the extractor succeeded but skipped some
    information (e.g. a protected PDF where we got page count but
    not text). ``error`` is set on hard failure; the dispatcher then
    marks ``metadata_status = "failed"`` and stashes the message on
    ``Attachment.metadata_error`` for surfacing in the admin UI.
    """

    common: Dict[str, Any] = field(default_factory=dict)
    type_specific: Dict[str, Any] = field(default_factory=dict)
    extracted_text: str = ""
    width: Optional[int] = None
    height: Optional[int] = None
    page_count: Optional[int] = None
    partial: bool = False
    error: str = ""

    def as_metadata_dict(self) -> Dict[str, Any]:
        """Return the ``Attachment.metadata`` wire shape for this result."""
        return {
            "common": self.common,
            "type_specific": self.type_specific,
        }


class AttachmentMetadataExtractor(ABC):
    """Abstract base for per-MIME extractors.

    Subclasses set ``mime_prefixes`` and/or ``exact_mimes`` so the
    dispatcher can route correctly. The dispatcher passes content via
    a temporary path so that extractors can stream from disk for large
    files (PDF / video) without holding the whole blob in memory.
    """

    name: str = "abstract"
    mime_prefixes: tuple[str, ...] = ()
    exact_mimes: frozenset[str] = frozenset()
    # Priority resolves ties when more than one extractor matches.
    # Higher = preferred. The default is 0; specialized extractors
    # (e.g. PDF, OOXML) should bump above the generic fallback.
    priority: int = 0

    def supports(self, mime: str) -> bool:
        """Whether this extractor handles ``mime``."""
        if not mime:
            return False
        if mime in self.exact_mimes:
            return True
        return any(mime.startswith(p) for p in self.mime_prefixes)

    @abstractmethod
    async def extract(
        self,
        *,
        path: str,
        mime: str,
        filename: str,
        size: int,
    ) -> MetadataResult:
        """Run extraction against the local file at ``path``.

        The dispatcher guarantees ``path`` exists and contains the
        attachment bytes for the duration of the call. Extractors must
        not hold a reference to the path beyond their return.
        """
        raise NotImplementedError
