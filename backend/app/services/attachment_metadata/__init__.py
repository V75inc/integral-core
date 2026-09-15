"""Metadata extraction pipeline for attachments (Phase 1.5).

Public surface:

    get_metadata_extraction_service()   — singleton dispatcher
    AttachmentMetadataExtractor          — ABC for new extractors
    MetadataResult                       — extractor return shape

Extractors register themselves by MIME prefix or exact type. The
dispatcher picks the best match, runs the extractor against the
attachment bytes (or, for very large files, the storage path), and
writes the result back onto the Attachment node:

    attachment.metadata = {"common": {...}, "type_specific": {...}}
    attachment.extracted_text = "..."
    attachment.metadata_status = "complete" | "partial" | "failed"
    attachment.metadata_extractor_version = settings....

The dispatch path runs as an asyncio background task — uploads return
immediately and the metadata becomes available a moment later. Re-runs
are idempotent (overwrites the previous result) and triggered by the
``/attachments/{id}/reprocess`` endpoint or by bumping
``ATTACHMENT_METADATA_EXTRACTOR_VERSION``.

OCR, ML-derived labels, transcription, embeddings — explicitly out of
scope for this phase but the ABC accommodates them without schema
churn (just register a new extractor that targets ``image/*`` or
``audio/*``). On-demand transcription for the agent exists separately —
``integral_transcribe_audio`` in ``app/agentive/services/speech`` — and
deliberately persists nothing, so it is not an extractor.
"""

from app.services.attachment_metadata.base import (
    AttachmentMetadataExtractor,
    MetadataResult,
)
from app.services.attachment_metadata.service import (
    MetadataExtractionService,
    get_metadata_extraction_service,
    reset_for_testing,
)

__all__ = [
    "AttachmentMetadataExtractor",
    "MetadataResult",
    "MetadataExtractionService",
    "get_metadata_extraction_service",
    "reset_for_testing",
]
