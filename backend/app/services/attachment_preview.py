"""Office-document → PDF preview service.

Backed by a headless LibreOffice subprocess. Office formats that have
no good in-browser viewer (pptx, ppt, odp, odt, doc, rtf) get a
server-rendered PDF preview cached at a sibling storage key:

    attachments/{entry_id}/{attachment_id}/_preview.pdf

Generated lazily on first request to ``GET /attachments/{id}/preview``.
Subsequent requests stream the cached PDF directly. Re-generation can
be forced via ``ensure_preview(force=True)``.

When LibreOffice isn't installed (typical in slim CI containers), the
service raises ``PreviewUnavailableError`` and the HTTP endpoint
surfaces a 503. Document this in the install instructions —
backend/README.md.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional
from weakref import WeakKeyDictionary

from app.config import settings
from app.models.nodes import Attachment
from app.services.attachment_storage import (
    AttachmentStorageService,
    get_attachment_storage_service,
)

logger = logging.getLogger(__name__)


PREVIEW_FILENAME = "_preview.pdf"
PREVIEW_TIMEOUT_SECONDS = 60

# MIME types that can be (a) profitably previewed in-browser as PDF,
# and (b) handled by LibreOffice headless conversion. PDFs themselves
# are excluded — the original is the preview.
_PREVIEWABLE_VIA_LIBREOFFICE = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.ms-powerpoint",
        "application/vnd.oasis.opendocument.presentation",
        "application/vnd.oasis.opendocument.text",
        "application/msword",
        "application/rtf",
        "text/rtf",
        # Modern Word (the format the document renderer produces) and the
        # other office formats LibreOffice can turn into a PDF faithfully.
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "application/vnd.ms-word.document.macroenabled.12",
        "application/vnd.ms-powerpoint.presentation.macroenabled.12",
        "application/vnd.openxmlformats-officedocument.presentationml.template",
        "application/vnd.oasis.opendocument.graphics",
        "application/vnd.ms-visio.drawing",
        "application/vnd.visio",
        "application/vnd.ms-publisher",
    }
)


class PreviewUnavailableError(RuntimeError):
    """Raised when conversion can't proceed (binary missing, timeout, etc.)."""


def can_preview(mime: str) -> bool:
    """Whether the MIME type has a server-rendered preview path.

    Used by the upload endpoint to decide whether to surface a
    ``preview_url`` to the frontend. Doesn't probe the binary — that
    happens lazily on first request — so the answer is purely a
    function of the MIME.
    """
    return (mime or "") in _PREVIEWABLE_VIA_LIBREOFFICE


def _resolve_libreoffice_binary() -> Optional[str]:
    """Best-effort lookup for the headless LibreOffice executable.

    Different distros / package managers use different names:
    ``libreoffice``, ``soffice``, ``/usr/bin/soffice``. We try the
    common names in order. Returns absolute path or None.
    """
    for name in ("libreoffice", "soffice"):
        path = shutil.which(name)
        if path:
            return path
    return None


_PREVIEW_RUNS: WeakKeyDictionary = WeakKeyDictionary()
_PREVIEW_SLOTS: WeakKeyDictionary = WeakKeyDictionary()


async def ensure_preview(
    attachment: Attachment,
    *,
    entry_id: str,
    storage: Optional[AttachmentStorageService] = None,
    force: bool = False,
) -> Optional[str]:
    """Coalesce concurrent preview reads without moving graph writes to a task."""
    if (
        attachment.source_type != "file"
        or not can_preview(attachment.mime_type)
        or not attachment.storage_key
    ):
        return None
    svc = storage or get_attachment_storage_service()
    loop = asyncio.get_running_loop()
    runs = _PREVIEW_RUNS.setdefault(loop, {})
    key = (entry_id, attachment.id, attachment.storage_key, id(svc))
    existing = runs.get(key)
    if existing is not None:
        result = await asyncio.shield(existing)
        if result:
            attachment.preview_storage_key = result
        return result
    future = loop.create_future()
    future.add_done_callback(
        lambda done: None if done.cancelled() else done.exception()
    )
    runs[key] = future
    try:
        result = await _ensure_preview(
            attachment, entry_id=entry_id, storage=svc, force=force
        )
        future.set_result(result)
        return result
    except asyncio.CancelledError:
        future.cancel()
        raise
    except Exception as exc:
        future.set_exception(exc)
        raise
    finally:
        runs.pop(key, None)


async def _ensure_preview(
    attachment: Attachment,
    *,
    entry_id: str,
    storage: Optional[AttachmentStorageService] = None,
    force: bool = False,
) -> Optional[str]:
    """Return the storage key for the cached preview PDF.

    Generates on first call when no cached preview exists yet. Returns
    None when the attachment isn't previewable; raises
    PreviewUnavailableError when the binary is missing or conversion
    fails.

    """
    if attachment.source_type != "file":
        return None
    if not can_preview(attachment.mime_type):
        return None
    if not attachment.storage_key:
        return None

    svc = storage or get_attachment_storage_service()

    cached_key = attachment.preview_storage_key
    if cached_key and not force:
        existing = await svc.get_metadata(cached_key)
        if existing is not None:
            return cached_key
        # The stored bytes vanished — fall through and regenerate.

    binary = _resolve_libreoffice_binary()
    if binary is None:
        raise PreviewUnavailableError(
            "LibreOffice headless binary not found on PATH "
            "(checked: libreoffice, soffice)."
        )

    if attachment.size > settings.ATTACHMENT_PREVIEW_MAX_BYTES:
        raise PreviewUnavailableError(
            "File exceeds the automatic preview size limit; download the original."
        )
    loop = asyncio.get_running_loop()
    limiter = _PREVIEW_SLOTS.setdefault(loop, [asyncio.Semaphore(1), 0])
    limiter[1] += 1
    try:
        async with limiter[0]:
            blob = await svc.read_attachment(attachment.storage_key)
            if blob is None:
                raise PreviewUnavailableError("Attachment bytes not found in storage.")
            pdf_bytes = await _convert_to_pdf(
                binary, blob, suffix=_suffix_for(attachment)
            )
    finally:
        limiter[1] -= 1
        if limiter[1] == 0:
            _PREVIEW_SLOTS.pop(loop, None)

    sibling_key = svc.build_sibling_key(entry_id, attachment.id, PREVIEW_FILENAME)
    result = await svc._storage.save_file(  # noqa: SLF001 - intentional reach-in
        sibling_key,
        pdf_bytes,
        metadata={
            "entry_id": entry_id,
            "attachment_id": attachment.id,
            "kind": "preview",
            "content_type": "application/pdf",
        },
    )
    persisted_key = str(result.get("path") or sibling_key)
    attachment.preview_storage_key = persisted_key
    await attachment.save()
    return persisted_key


def _suffix_for(attachment: Attachment) -> str:
    """Return the file-extension suffix LibreOffice needs to pick its importer.

    The bytes are authoritative but the binary inspects the suffix on
    the temp file to choose the right import filter, so we preserve
    the original extension end-to-end.
    """
    name = attachment.filename or ""
    ext = os.path.splitext(name)[1]
    if ext:
        return ext.lower()
    # Fall back to MIME-derived extension for common cases.
    mime_to_ext = {
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
        "application/vnd.ms-powerpoint": ".ppt",
        "application/vnd.oasis.opendocument.presentation": ".odp",
        "application/vnd.oasis.opendocument.text": ".odt",
        "application/msword": ".doc",
        "application/rtf": ".rtf",
        "text/rtf": ".rtf",
    }
    return mime_to_ext.get(attachment.mime_type, "")


async def _convert_to_pdf(binary: str, content: bytes, *, suffix: str) -> bytes:
    """Spawn ``libreoffice --headless --convert-to pdf`` and return PDF bytes."""
    directory = tempfile.TemporaryDirectory(prefix="integral-preview-")
    workdir = directory.name
    try:
        src_path = os.path.join(workdir, f"source{suffix or '.bin'}")
        write = asyncio.create_task(
            asyncio.to_thread(Path(src_path).write_bytes, content)
        )
        try:
            await asyncio.shield(write)
        except asyncio.CancelledError:
            await write
            raise

        # ``--outdir`` writes ``source.pdf`` next to the source file —
        # we control the dir so collisions with other in-flight
        # conversions are impossible (TemporaryDirectory gives us a
        # unique parent).
        cmd = [
            binary,
            f"-env:UserInstallation={Path(workdir, 'profile').as_uri()}",
            "--headless",
            "--nologo",
            "--nofirststartwizard",
            "--convert-to",
            "pdf",
            "--outdir",
            workdir,
            src_path,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=PREVIEW_TIMEOUT_SECONDS
                )
            except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
                with __import__("contextlib").suppress(ProcessLookupError):
                    proc.kill()
                await proc.communicate()
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise PreviewUnavailableError(
                    f"LibreOffice timed out after {PREVIEW_TIMEOUT_SECONDS}s."
                )
        except FileNotFoundError as e:
            raise PreviewUnavailableError(f"LibreOffice binary missing: {e}") from e

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            raise PreviewUnavailableError(
                f"LibreOffice conversion failed (rc={proc.returncode}): " f"{err[:400]}"
            )

        # The PDF lives at <workdir>/source.pdf
        pdf_path = os.path.join(workdir, "source.pdf")
        if not os.path.isfile(pdf_path):
            # Some libreoffice versions name the output after the input
            # base — scan the dir as a fallback.
            candidates = [f for f in os.listdir(workdir) if f.lower().endswith(".pdf")]
            if not candidates:
                raise PreviewUnavailableError("LibreOffice produced no PDF output.")
            pdf_path = os.path.join(workdir, candidates[0])

        if os.path.getsize(pdf_path) > settings.ATTACHMENT_PREVIEW_MAX_BYTES:
            raise PreviewUnavailableError(
                "Generated preview exceeds the preview size limit."
            )
        return await asyncio.to_thread(Path(pdf_path).read_bytes)

    finally:
        await asyncio.to_thread(directory.cleanup)
