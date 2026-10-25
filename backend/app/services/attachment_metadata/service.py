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


def _write_extraction_input(blob: bytes, suffix: str) -> str:
    """Write parser input off-loop, removing incomplete files on failure."""
    temp = tempfile.NamedTemporaryFile(mode="wb", suffix=suffix, delete=False)
    try:
        with temp:
            temp.write(blob)
        return temp.name
    except BaseException:
        try:
            os.unlink(temp.name)
        except OSError:
            pass
        raise


class MetadataExtractionService:
    """Orchestrates extractor selection + execution + persistence."""

    def __init__(
        self,
        extractors: tuple[AttachmentMetadataExtractor, ...] = BUILTIN_EXTRACTORS,
    ):
        self._extractors = extractors
        # Tracks in-flight extractions so the same attachment is not
        # processed concurrently. Keys: attachment_id.
        self._in_flight: dict[str, asyncio.Future] = {}
        self._tasks: set[asyncio.Task] = set()
        # Bound simultaneous blobs/parsers across all tenants in this process.
        self._slots = asyncio.Semaphore(
            max(1, min(settings.ATTACHMENT_METADATA_CONCURRENCY, 16))
        )

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
        future = loop.create_future()
        # Background failures are logged; retrieve exceptions even without a waiter.
        future.add_done_callback(
            lambda done: None if done.cancelled() else done.exception()
        )
        self._in_flight[attachment_id] = future
        task = loop.create_task(self._background_runner(attachment_id, future))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def extract_now(
        self,
        attachment_id: str,
        *,
        force_analysis: bool = False,
        analysis_principal_id: str | None = None,
    ) -> Attachment:
        """Join overlapping extraction; a later explicit reprocess still runs anew."""
        existing = self._in_flight.get(attachment_id)
        if existing is not None:
            return await asyncio.shield(existing)
        future = asyncio.get_running_loop().create_future()
        future.add_done_callback(
            lambda done: None if done.cancelled() else done.exception()
        )
        self._in_flight[attachment_id] = future
        return await self._complete_extraction(
            attachment_id,
            future,
            force_analysis=force_analysis,
            analysis_principal_id=analysis_principal_id,
        )

    async def _complete_extraction(
        self,
        attachment_id: str,
        future: asyncio.Future,
        *,
        force_analysis: bool = False,
        analysis_principal_id: str | None = None,
    ) -> Attachment:
        try:
            async with self._slots:
                result = await self._run_extraction(attachment_id)
            from .analysis import enqueue_analysis

            # One completed extraction schedules one transform. The explicit
            # reprocess path must not also enqueue an automatic duplicate.
            await enqueue_analysis(
                result, force=force_analysis, principal_id=analysis_principal_id
            )
            future.set_result(result)
            return result
        except asyncio.CancelledError:
            try:
                await self._record_failure(
                    attachment_id,
                    "Metadata extraction was interrupted. Reprocess the file to try again.",
                )
            finally:
                future.cancel()
            raise
        except Exception as exc:
            await self._record_failure(
                attachment_id,
                "Metadata extraction could not complete. Reprocess the file to try again.",
            )
            future.set_exception(exc)
            raise
        finally:
            if self._in_flight.get(attachment_id) is future:
                del self._in_flight[attachment_id]

    async def _record_failure(self, attachment_id: str, message: str) -> None:
        """Do not leave a durable processing indicator after failed/cancelled work."""
        try:
            attachment = await Attachment.find_one({"id": attachment_id})
            if attachment is not None and attachment.metadata_status == "processing":
                # Diagnostic cleanup only, including after cancellation: never
                # publish text, model output or a new provider request here.
                context = await attachment.get_context()
                await context.database.find_one_and_update(
                    "node",
                    {
                        "id": attachment.id,
                        "entity": "Attachment",
                        "context.metadata_status": "processing",
                        "context.content_hash": attachment.content_hash,
                        "context.storage_key": attachment.storage_key,
                    },
                    {
                        "$set": {
                            "context.metadata_status": "failed",
                            "context.metadata_error": message,
                        }
                    },
                )
        except Exception:
            logger.exception("Could not record metadata failure for %s", attachment_id)

    async def _background_runner(
        self, attachment_id: str, future: asyncio.Future
    ) -> None:
        try:
            await self._complete_extraction(attachment_id, future)
        except Exception:
            logger.exception(
                "metadata extraction background task failed for %s", attachment_id
            )

    def _pick_extractor(self, mime: str) -> Optional[AttachmentMetadataExtractor]:
        candidates = [e for e in self._extractors if e.supports(mime)]
        if not candidates:
            return None
        candidates.sort(key=lambda e: e.priority, reverse=True)
        return candidates[0]

    async def _save_metadata(self, attachment: Attachment) -> None:
        """Update derived fields only; a late parser cannot resurrect a deleted file."""
        from jvspatial.core.context import get_default_context

        from .analysis import in_analysis

        fields = {
            key: getattr(attachment, key)
            for key in (
                "metadata",
                "extracted_text",
                "metadata_status",
                "metadata_error",
                "metadata_extractor_version",
                "width",
                "height",
                "page_count",
            )
        }

        async def persist():
            row = await get_default_context().database.find_one_and_update(
                "node",
                {
                    "id": attachment.id,
                    "entity": "Attachment",
                    "context.storage_key": attachment.storage_key,
                    "context.content_hash": attachment.content_hash,
                    "context.scan_status": {"$ne": "blocked"},
                },
                {"$set": {f"context.{key}": value for key, value in fields.items()}},
            )
            if row is None:
                raise ValueError("Attachment changed or was removed during extraction")
            if attachment.metadata_status == "processing":
                return None
            from app.services.retrieval.indexing import enqueue_entry

            from .analysis import file_parent

            entry, _ = await file_parent(attachment)
            if entry is not None:
                # Selected text and its durable index intent commit together.
                await enqueue_entry(entry)
            return entry

        execution = in_analysis.get()
        if execution is not None:
            from app.agentive.services.work_items import authorized_work_item_effect

            async with authorized_work_item_effect(execution):
                await persist()
        else:
            from app.services.entry_write_scope import entry_write_scope

            async with entry_write_scope(attachment.id):
                await persist()

    async def _run_extraction(self, attachment_id: str) -> Attachment:
        attachment = await Attachment.find_one({"id": attachment_id})
        if not attachment:
            raise ValueError(f"Attachment {attachment_id} not found")
        # Keep the extraction's file identity independent of any entity instance
        # another task obtains through the graph cache during the parser await.
        attachment = attachment.model_copy()
        if attachment.scan_status == "blocked":
            return attachment

        # URL attachments and entries without persisted bytes have nothing
        # to extract; mark skipped so the UI can still surface a status.
        if attachment.source_type != "file" or not attachment.storage_key:
            attachment.metadata_status = "skipped"
            attachment.metadata_extractor_version = (
                settings.ATTACHMENT_METADATA_EXTRACTOR_VERSION
            )
            await self._save_metadata(attachment)
            return attachment

        attachment.metadata_status = "processing"
        attachment.metadata_error = ""
        await self._save_metadata(attachment)

        extractor = self._pick_extractor(attachment.mime_type)
        if (
            extractor is None
            or attachment.size > settings.ATTACHMENT_METADATA_MAX_BYTES
        ):
            attachment.metadata_status = "skipped"
            attachment.metadata_error = (
                "File exceeds the metadata extraction size limit. The original remains available."
                if extractor is not None
                else ""
            )
            attachment.metadata_extractor_version = (
                settings.ATTACHMENT_METADATA_EXTRACTOR_VERSION
            )
            await self._save_metadata(attachment)
            return attachment

        storage = get_attachment_storage_service()
        blob = await storage.read_attachment(attachment.storage_key)
        if blob is None:
            attachment.metadata_status = "failed"
            attachment.metadata_error = "Attachment bytes not found in storage."
            await self._save_metadata(attachment)
            return attachment

        # Write blob to a temp file so extractors that prefer disk
        # access (pdfplumber, mutagen, ffprobe) don't have to deal with
        # in-memory wrappers.
        suffix = os.path.splitext(attachment.filename or "")[1] or ""
        write = asyncio.create_task(
            asyncio.to_thread(_write_extraction_input, blob, suffix)
        )
        try:
            input_path = await asyncio.shield(write)
        except asyncio.CancelledError:
            input_path = await write
            await asyncio.to_thread(os.unlink, input_path)
            raise
        try:
            try:
                result: MetadataResult = await extractor.extract(
                    path=input_path,
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
                await self._save_metadata(attachment)
                return attachment
        finally:
            try:
                await asyncio.to_thread(os.unlink, input_path)
            except OSError:
                pass

        # ---- Persist result ----
        attachment.metadata = result.as_metadata_dict()
        cap = settings.ATTACHMENT_EXTRACTED_TEXT_MAX_BYTES
        text = result.extracted_text or ""
        if len(text.encode("utf-8")) > cap:
            text = text.encode("utf-8")[:cap].decode("utf-8", errors="ignore")
            result.partial = True
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
        await self._save_metadata(attachment)
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
