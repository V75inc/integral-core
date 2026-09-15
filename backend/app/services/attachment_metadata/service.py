"""Metadata extraction service: dispatch, queue, persist.

This is the single entry point for everything that wants to run
metadata extraction. The two public verbs:

    schedule_extraction(attachment_id)  — fire-and-forget; runs on the
        process's asyncio event loop in the background. Used from the
        upload endpoints so HTTP responses return immediately.

    extract_now(attachment_id)          — awaitable; runs inline. Used
        by /attachments/{id}/reprocess and by tests.

Both paths flow through ``_run_extraction`` which is idempotent:
re-running on a finished attachment overwrites the previous result and
bumps ``metadata_extractor_version`` to the current setting.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import Optional

from app.config import settings
from app.models.nodes import Attachment
from app.services.attachment_metadata.base import (
    AttachmentMetadataExtractor,
    MetadataResult,
)
from app.services.attachment_metadata.extractors import BUILTIN_EXTRACTORS
from app.services.attachment_storage import get_attachment_storage_service

logger = logging.getLogger(__name__)


class MetadataExtractionService:
    """Orchestrates extractor selection + execution + persistence."""

    def __init__(
        self,
        extractors: tuple[AttachmentMetadataExtractor, ...] = BUILTIN_EXTRACTORS,
    ):
        self._extractors = extractors
        # Tracks in-flight extractions so the same attachment is not
        # processed concurrently. Keys: attachment_id.
        self._in_flight: set[str] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def schedule_extraction(self, attachment_id: str) -> None:
        """Fire-and-forget background run."""
        if not attachment_id:
            return
        if attachment_id in self._in_flight:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "schedule_extraction called outside an event loop; "
                "metadata for %s will not be extracted in the background.",
                attachment_id,
            )
            return
        self._in_flight.add(attachment_id)
        loop.create_task(self._background_runner(attachment_id))

    async def extract_now(self, attachment_id: str) -> Attachment:
        """Run extraction inline and return the updated Attachment."""
        if attachment_id in self._in_flight:
            # Wait for the in-flight task by polling — keeps this
            # contention path simple. In practice callers rarely race.
            for _ in range(50):  # ~5 s
                await asyncio.sleep(0.1)
                if attachment_id not in self._in_flight:
                    break
        self._in_flight.add(attachment_id)
        try:
            return await self._run_extraction(attachment_id)
        finally:
            self._in_flight.discard(attachment_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _background_runner(self, attachment_id: str) -> None:
        try:
            await self._run_extraction(attachment_id)
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "metadata extraction background task failed for %s: %s",
                attachment_id,
                e,
            )
        finally:
            self._in_flight.discard(attachment_id)

    def _pick_extractor(self, mime: str) -> Optional[AttachmentMetadataExtractor]:
        candidates = [e for e in self._extractors if e.supports(mime)]
        if not candidates:
            return None
        candidates.sort(key=lambda e: e.priority, reverse=True)
        return candidates[0]

    async def _run_extraction(self, attachment_id: str) -> Attachment:
        attachment = await Attachment.get(attachment_id)
        if not attachment:
            raise ValueError(f"Attachment {attachment_id} not found")

        # URL attachments and entries without persisted bytes have nothing
        # to extract; mark skipped so the UI can still surface a status.
        if attachment.source_type != "file" or not attachment.storage_key:
            attachment.metadata_status = "skipped"
            attachment.metadata_extractor_version = (
                settings.ATTACHMENT_METADATA_EXTRACTOR_VERSION
            )
            await attachment.save()
            return attachment

        attachment.metadata_status = "processing"
        attachment.metadata_error = ""
        await attachment.save()

        storage = get_attachment_storage_service()
        blob = await storage.read_attachment(attachment.storage_key)
        if blob is None:
            attachment.metadata_status = "failed"
            attachment.metadata_error = "Attachment bytes not found in storage."
            await attachment.save()
            return attachment

        extractor = self._pick_extractor(attachment.mime_type)
        if extractor is None:
            attachment.metadata_status = "skipped"
            attachment.metadata_extractor_version = (
                settings.ATTACHMENT_METADATA_EXTRACTOR_VERSION
            )
            await attachment.save()
            return attachment

        # Write blob to a temp file so extractors that prefer disk
        # access (pdfplumber, mutagen, ffprobe) don't have to deal with
        # in-memory wrappers.
        suffix = os.path.splitext(attachment.filename or "")[1] or ""
        tmp = tempfile.NamedTemporaryFile(mode="wb", suffix=suffix, delete=False)
        try:
            tmp.write(blob)
            tmp.flush()
            tmp.close()
            try:
                result: MetadataResult = await extractor.extract(
                    path=tmp.name,
                    mime=attachment.mime_type,
                    filename=attachment.filename,
                    size=attachment.size,
                )
            except Exception as e:  # noqa: BLE001
                logger.exception(
                    "extractor %s failed for %s: %s",
                    extractor.name,
                    attachment_id,
                    e,
                )
                attachment.metadata_status = "failed"
                attachment.metadata_error = f"{extractor.name}: {e}"
                attachment.metadata_extractor_version = (
                    settings.ATTACHMENT_METADATA_EXTRACTOR_VERSION
                )
                await attachment.save()
                return attachment
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

        # ---- Persist result ----
        attachment.metadata = result.as_metadata_dict()
        cap = settings.ATTACHMENT_EXTRACTED_TEXT_MAX_BYTES
        text = result.extracted_text or ""
        if len(text.encode("utf-8")) > cap:
            text = text.encode("utf-8")[:cap].decode("utf-8", errors="replace")
        attachment.extracted_text = text

        if result.width is not None:
            attachment.width = result.width
        if result.height is not None:
            attachment.height = result.height
        if result.page_count is not None:
            attachment.page_count = result.page_count

        if result.error:
            attachment.metadata_status = "failed"
            attachment.metadata_error = result.error
        elif result.partial:
            attachment.metadata_status = "partial"
            attachment.metadata_error = ""
        else:
            attachment.metadata_status = "complete"
            attachment.metadata_error = ""
        attachment.metadata_extractor_version = (
            settings.ATTACHMENT_METADATA_EXTRACTOR_VERSION
        )
        await attachment.save()
        return attachment


_SERVICE: Optional[MetadataExtractionService] = None


def get_metadata_extraction_service() -> MetadataExtractionService:
    """Return the process-wide metadata extraction service."""
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = MetadataExtractionService()
    return _SERVICE


def reset_for_testing(
    extractors: Optional[tuple[AttachmentMetadataExtractor, ...]] = None,
) -> None:
    """Reset the singleton (optionally with a custom extractor set).

    Tests use this to substitute a deterministic extractor without
    pulling in real third-party libraries.
    """
    global _SERVICE
    if extractors is None:
        _SERVICE = None
    else:
        _SERVICE = MetadataExtractionService(extractors=extractors)
