"""Attachment API endpoints with file upload/download.

Phase 1 + 1.5 (file handling, Plan 03):
    - Per-file ceiling raised to ``settings.ATTACHMENT_MAX_UPLOAD_BYTES``
      (default 500 MB) with a batch endpoint capped at
      ``ATTACHMENT_MAX_BATCH_FILES`` / ``ATTACHMENT_MAX_BATCH_BYTES``.
    - Server-side MIME sniffing via libmagic. The client's Content-Type
      is advisory; the sniffed type wins (and a mismatch is rejected).
    - SHA-256 content hash captured on every upload; uploads that
      duplicate a hash already attached to the same entry are rejected
      with 409 to keep the per-entry list tidy.
    - Pluggable malware scanner runs post-write, pre-publish; uploads
      with ``scan_status == "blocked"`` are filtered out of every
      user-facing route (they remain in the graph for audit).
    - Metadata extraction (Phase 1.5) is dispatched as an asyncio
      background task so the HTTP response returns immediately.
"""

import contextlib
import io
import zipfile
from datetime import datetime
from typing import Any, Dict, List, Optional, cast
from urllib.parse import quote, urlparse

from fastapi import Request, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse
from jvspatial.api import endpoint
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    MissingAuthenticationError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.api.utils import export_node, resolve_principal_id
from app.config import settings
from app.models.edges import HAS_ATTACHMENT
from app.models.nodes import Attachment, ChatThread, Entry
from app.schemas.policy import Resource, Subject
from app.services.attachment_metadata import get_metadata_extraction_service
from app.services.attachment_preview import (
    PreviewUnavailableError,
    can_preview,
    ensure_preview,
)
from app.services.attachment_scanner import (
    SCAN_STATUS_BLOCKED,
    get_attachment_scanner,
)
from app.services.attachment_storage import get_attachment_storage_service
from app.services.attachment_thumbnails import ensure_thumbnail
from app.services.attachment_upload_shared import (
    read_upload_streaming as _read_upload_streaming,
)
from app.services.attachment_upload_shared import (
    resolve_effective_mime as _resolve_effective_mime,
)
from app.services.attachment_urls import enrich_attachment_export, is_visible_to_user
from app.services.change_event import emit_change_event
from app.services.chat_threads import get_thread as chat_threads_get_thread
from app.services.chunked_upload import append_chunk as chunked_append_chunk
from app.services.chunked_upload import assemble_session as chunked_assemble_session
from app.services.chunked_upload import cancel_session as chunked_cancel_session
from app.services.chunked_upload import clear_chunks as chunked_clear_chunks
from app.services.chunked_upload import get_session as chunked_get_session
from app.services.chunked_upload import session_to_dict as chunked_session_to_dict
from app.services.chunked_upload import start_session as chunked_start_session
from app.services.policy_engine import evaluate as policy_evaluate
from app.services.url_safety import validate_public_http_url
from app.services.workspace_storage_usage import check_quota as check_workspace_quota
from app.services.workspace_storage_usage import (
    check_quota_for_entry,
    decrement_for_entry,
)
from app.services.workspace_storage_usage import increment as increment_workspace_usage
from app.services.workspace_storage_usage import (
    increment_for_entry,
)
from app.utils.time import utc_now_iso


async def _find_duplicate_hash_on_entry(
    entry: Entry, content_hash: str
) -> Optional[Attachment]:
    """Return an existing attachment on ``entry`` matching ``content_hash``."""
    if not content_hash or not entry.attachment_ids:
        return None
    existing = await entry.nodes(edge=["HAS_ATTACHMENT"], direction="out")
    for att in existing:
        if (
            isinstance(att, Attachment)
            and getattr(att, "content_hash", "") == content_hash
        ):
            return att
    return None


async def _persist_uploaded_file(
    *,
    entry: Entry,
    user_id: str,
    file: UploadFile,
) -> Dict[str, Any]:
    """Shared single-file persist path used by both upload endpoints.

    Returns a dict with either ``attachment`` (success) or ``error``
    (single-file failure, used by the batch endpoint to keep going).
    """
    content, filename, sha256_hex, head_bytes = await _read_upload_streaming(
        file, max_bytes=settings.ATTACHMENT_MAX_UPLOAD_BYTES
    )
    file_size = len(content)
    mime_type = _resolve_effective_mime(
        head_bytes=head_bytes,
        content_type_claim=file.content_type or "",
        filename=filename,
    )

    # Per-entry dedup (Phase 1: SHA-256 scope = same entry only).
    existing = await _find_duplicate_hash_on_entry(entry, sha256_hex)
    if existing is not None:
        raise ResourceConflictError(
            message=("An attachment with identical content is already on this entry."),
        )

    # Plan 03 — Phase 5: enforce org storage quota when configured.
    # ``check_quota_for_entry`` is a fast O(1) read against the
    # denormalised counter and returns None when there's nothing to
    # enforce (no org, unlimited quota, or enforcement disabled).
    over_quota = await check_quota_for_entry(entry, file_size)
    if over_quota is not None:
        raise BadRequestError(
            message=(
                "Workspace storage quota exceeded "
                f"({over_quota.bytes_used} / {over_quota.quota_bytes} bytes). "
                "Delete files or contact an admin to raise the quota."
            ),
        )

    attachment = await Attachment.create(
        filename=filename,
        mime_type=mime_type,
        size=file_size,
        storage_key="",
        source_type="file",
        external_url="",
        uploaded_by=user_id,
        content_hash=sha256_hex,
        scan_status="pending",
        metadata_status="pending",
        created_at=utc_now_iso(),
    )
    storage = get_attachment_storage_service()
    try:
        result = await storage.save_attachment(
            entry_id=entry.id,
            attachment_id=attachment.id,
            filename=filename,
            content=content,
            mime_type=mime_type,
            metadata={
                "entry_id": entry.id,
                "attachment_id": attachment.id,
                "uploaded_by": user_id,
                "sha256": sha256_hex,
            },
        )
    except Exception:
        await attachment.delete()
        raise

    attachment.storage_key = str(result.get("path") or "")
    await attachment.save()

    # ---- Post-write, pre-publish scan ----
    scanner = get_attachment_scanner()
    try:
        verdict = await scanner.scan(
            content=content,
            mime_type=mime_type,
            filename=filename,
        )
    except Exception as e:  # noqa: BLE001
        # Scanner failure shouldn't block the upload — surface in the
        # attachment record so an admin can re-scan later.
        attachment.scan_status = "failed"
        attachment.scan_engine = scanner.name
        attachment.scan_message = f"scanner crashed: {e}"
    else:
        attachment.scan_status = verdict.status
        attachment.scan_engine = verdict.engine
        attachment.scan_message = verdict.message
        if verdict.is_blocked:
            # Keep the metadata record (for audit) but remove the bytes.
            with contextlib.suppress(Exception):
                await storage.delete_attachment(attachment.storage_key)
            attachment.storage_key = ""
    await attachment.save()

    # ---- Generate image thumbnail (Phase 2) ----
    # Cheap enough to run inline — Pillow downscale of a single image
    # is sub-100ms on a midsize JPEG. Done before linking so the
    # exported attachment carries thumb_storage_key on the create
    # response. Failure here is non-fatal (the helper logs and returns
    # None when Pillow isn't installed or the image is malformed).
    if attachment.scan_status != SCAN_STATUS_BLOCKED:
        try:
            thumb_key = await ensure_thumbnail(
                attachment, entry_id=entry.id, content=content, storage=storage
            )
            if thumb_key:
                await attachment.save()
        except Exception:  # noqa: BLE001
            # Defensive: the helper already catches/logs; if something
            # truly unexpected escapes, keep the upload alive.
            pass

    # ---- Link to entry ----
    await entry.connect(
        attachment,
        edge=HAS_ATTACHMENT,
        attached_at=utc_now_iso(),
        attached_by=user_id,
    )
    if attachment.id not in entry.attachment_ids:
        entry.attachment_ids.append(attachment.id)
        await entry.save()

    # ---- D-05 single emission path ----
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="attachment.create",
        resource_type="Attachment",
        resource_id=attachment.id,
        before=None,
        after=await export_node(attachment),
        scope=f"track:{entry.track_id or ''}",
    )

    # ---- Dispatch metadata extraction (background, non-blocking) ----
    if attachment.scan_status != SCAN_STATUS_BLOCKED:
        get_metadata_extraction_service().schedule_extraction(attachment.id)

    # ---- Counter accounting (Phase 5) ----
    # Only count bytes that actually landed in storage. Blocked uploads
    # had their bytes deleted by the scanner branch above, so they
    # don't accrue against quota.
    if attachment.scan_status != SCAN_STATUS_BLOCKED and attachment.storage_key:
        await increment_for_entry(entry, file_size)

    return {"attachment": await export_node(attachment)}


async def _read_multipart_files(request: Request, field_name: str) -> List[UploadFile]:
    """Pull ``UploadFile`` parts out of a multipart request body.

    jvspatial's ``@endpoint`` decorator generates an ``application/json``
    request body schema for handler params even when one is typed as
    ``UploadFile``, so the FastAPI binding never fires for our upload
    routes and the request arrives at the handler with ``file=None``.
    Sidestepping the binding entirely — call ``request.form()`` and
    pluck the multipart parts directly — keeps the upload path working
    until upstream jvspatial gains multipart awareness.
    """
    try:
        form = await request.form()
    except Exception as e:  # noqa: BLE001
        raise BadRequestError(message=f"Could not parse multipart body: {e}")
    parts: List[UploadFile] = []
    for value in form.getlist(field_name):
        # ``fastapi.UploadFile`` is a subclass of
        # ``starlette.datastructures.UploadFile`` (it adds the FastAPI
        # binding metaclass). Multipart values that arrive as the raw
        # starlette type are interface-compatible — same attributes,
        # same async API — so we cast on append rather than building a
        # second list. The isinstance guard ensures we never coerce a
        # plain string value.
        if isinstance(value, (UploadFile, StarletteUploadFile)):
            parts.append(cast(UploadFile, value))
    return parts


@endpoint(
    "/entries/{entry_id}/attachments", methods=["POST"], auth=True, tags=["Attachments"]
)
async def upload_attachment(
    request: Request,
    entry_id: str,
) -> Dict[str, Any]:
    """Upload a single file attachment to an entry.

    The handler parses the multipart body itself instead of relying on
    FastAPI's ``File(...)`` parameter binding — see
    ``_read_multipart_files`` for why.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.update",
        resource=Resource(kind="entry", id=entry_id, scope=f"entry:{entry_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(
            message="You do not have permission to add attachments to this entry"
        )

    entry = await Entry.get(entry_id)
    if not entry:
        raise ResourceNotFoundError(message="Entry not found")

    files = await _read_multipart_files(request, "file")
    if not files:
        raise BadRequestError(message="Multipart form must include a 'file' part.")

    result = await _persist_uploaded_file(
        entry=entry,
        user_id=user_id,
        file=files[0],
    )
    return {**result, "message": "Attachment uploaded successfully"}


@endpoint(
    "/entries/{entry_id}/attachments/batch",
    methods=["POST"],
    auth=True,
    tags=["Attachments"],
)
async def upload_attachments_batch(
    request: Request,
    entry_id: str,
) -> Dict[str, Any]:
    """Upload multiple files to an entry in a single request.

    Partial success is permitted: each file gets an independent result
    object (``{"attachment": ...}`` or ``{"error": ...}``). The endpoint
    returns 200 as long as the request itself is well-formed; per-file
    failures (oversize, mime-mismatch, dedup conflict) show up in
    ``results[].error``. Aggregate caps apply across the batch.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.update",
        resource=Resource(kind="entry", id=entry_id, scope=f"entry:{entry_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(
            message="You do not have permission to add attachments to this entry"
        )

    files = await _read_multipart_files(request, "files")
    if not files:
        raise BadRequestError(message="At least one file is required.")
    if len(files) > settings.ATTACHMENT_MAX_BATCH_FILES:
        raise BadRequestError(
            message=(
                f"Batch exceeds maximum files per request "
                f"({settings.ATTACHMENT_MAX_BATCH_FILES})."
            ),
        )

    entry = await Entry.get(entry_id)
    if not entry:
        raise ResourceNotFoundError(message="Entry not found")

    results: List[Dict[str, Any]] = []
    running_bytes = 0
    successes = 0
    failures = 0
    for upload in files:
        # Read each file under the per-file cap. The aggregate check
        # uses the post-read size so we don't have to trust the
        # content-length header.
        try:
            persisted = await _persist_uploaded_file(
                entry=entry,
                user_id=user_id,
                file=upload,
            )
            att = persisted["attachment"]
            file_size = int(
                att.get("context", {}).get("size", 0) or att.get("size", 0) or 0
            )
            running_bytes += file_size
            if running_bytes > settings.ATTACHMENT_MAX_BATCH_BYTES:
                # Roll back this one entry so the running total
                # accurately reflects what's now on the entry.
                with contextlib.suppress(Exception):
                    att_id = att.get("id") or att.get("context", {}).get("id")
                    if att_id:
                        node = await Attachment.get(att_id)
                        if node:
                            await node.delete()
                results.append(
                    {
                        "error": (
                            f"Batch exceeds aggregate cap "
                            f"({settings.ATTACHMENT_MAX_BATCH_BYTES} bytes)."
                        ),
                        "filename": upload.filename,
                    }
                )
                failures += 1
                break
            results.append(persisted)
            successes += 1
        except Exception as e:  # noqa: BLE001
            results.append(
                {
                    "error": str(e),
                    "filename": upload.filename,
                }
            )
            failures += 1

    return {
        "results": results,
        "summary": {
            "total": len(files),
            "succeeded": successes,
            "failed": failures,
        },
        "message": (
            "All attachments uploaded"
            if failures == 0
            else f"{successes} succeeded, {failures} failed"
        ),
    }


async def _find_duplicate_hash_on_thread(
    thread: ChatThread, content_hash: str
) -> Optional[Attachment]:
    """Return an existing chat attachment on ``thread`` matching ``content_hash``."""
    if not content_hash:
        return None
    existing = await thread.nodes(edge=["HAS_ATTACHMENT"], direction="out")
    for att in existing:
        if (
            isinstance(att, Attachment)
            and getattr(att, "content_hash", "") == content_hash
        ):
            return att
    return None


async def _persist_uploaded_chat_file(
    *,
    thread: ChatThread,
    user_id: str,
    file: UploadFile,
) -> Dict[str, Any]:
    """Chat-thread-scoped sibling of ``_persist_uploaded_file``.

    Reuses the same MIME sniff / dedup / scan / thumbnail / metadata
    pipeline but wires the resulting ``Attachment`` (``owner_kind="chat"``)
    to the owning ``ChatThread`` instead of an ``Entry`` — see
    ``HAS_ATTACHMENT`` docstring and I-GRAPH-01.
    """
    content, filename, sha256_hex, head_bytes = await _read_upload_streaming(
        file, max_bytes=settings.ATTACHMENT_MAX_UPLOAD_BYTES
    )
    file_size = len(content)
    mime_type = _resolve_effective_mime(
        head_bytes=head_bytes,
        content_type_claim=file.content_type or "",
        filename=filename,
    )

    existing = await _find_duplicate_hash_on_thread(thread, sha256_hex)
    if existing is not None:
        # Idempotent: composer re-sends the same file on a follow-up turn.
        # Returning the prior attachment keeps attachment_ids[] valid for the
        # agent (integral_attach_uploaded_file_to_entry) instead of a 409 that
        # blocks the whole send.
        return {"attachment": await export_node(existing), "deduplicated": True}

    over_quota = await check_workspace_quota(thread.workspace_id, file_size)
    if over_quota is not None:
        raise BadRequestError(
            message=(
                "Workspace storage quota exceeded "
                f"({over_quota.bytes_used} / {over_quota.quota_bytes} bytes). "
                "Delete files or contact an admin to raise the quota."
            ),
        )

    attachment = await Attachment.create(
        filename=filename,
        mime_type=mime_type,
        size=file_size,
        storage_key="",
        source_type="file",
        external_url="",
        uploaded_by=user_id,
        owner_kind="chat",
        content_hash=sha256_hex,
        scan_status="pending",
        metadata_status="pending",
        created_at=utc_now_iso(),
    )
    storage = get_attachment_storage_service()
    try:
        result = await storage.save_attachment(
            entry_id=f"chat/{thread.id}",
            attachment_id=attachment.id,
            filename=filename,
            content=content,
            mime_type=mime_type,
            metadata={
                "chat_thread_id": thread.id,
                "attachment_id": attachment.id,
                "uploaded_by": user_id,
                "sha256": sha256_hex,
            },
        )
    except Exception:
        await attachment.delete()
        raise

    attachment.storage_key = str(result.get("path") or "")
    await attachment.save()

    scanner = get_attachment_scanner()
    try:
        verdict = await scanner.scan(
            content=content,
            mime_type=mime_type,
            filename=filename,
        )
    except Exception as e:  # noqa: BLE001
        attachment.scan_status = "failed"
        attachment.scan_engine = scanner.name
        attachment.scan_message = f"scanner crashed: {e}"
    else:
        attachment.scan_status = verdict.status
        attachment.scan_engine = verdict.engine
        attachment.scan_message = verdict.message
        if verdict.is_blocked:
            with contextlib.suppress(Exception):
                await storage.delete_attachment(attachment.storage_key)
            attachment.storage_key = ""
    await attachment.save()

    if attachment.scan_status != SCAN_STATUS_BLOCKED:
        try:
            thumb_key = await ensure_thumbnail(
                attachment,
                entry_id=f"chat/{thread.id}",
                content=content,
                storage=storage,
            )
            if thumb_key:
                await attachment.save()
        except Exception:  # noqa: BLE001
            pass

    await thread.connect(
        attachment,
        edge=HAS_ATTACHMENT,
        attached_at=utc_now_iso(),
        attached_by=user_id,
    )

    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="attachment.create",
        resource_type="Attachment",
        resource_id=attachment.id,
        before=None,
        after=await export_node(attachment),
        scope=f"chat_thread:{thread.id}",
    )

    if attachment.scan_status != SCAN_STATUS_BLOCKED:
        get_metadata_extraction_service().schedule_extraction(attachment.id)

    if attachment.scan_status != SCAN_STATUS_BLOCKED and attachment.storage_key:
        await increment_workspace_usage(thread.workspace_id, file_size)

    return {"attachment": await export_node(attachment)}


@endpoint(
    "/chat/threads/{thread_id}/attachments",
    methods=["POST"],
    auth=True,
    tags=["Attachments", "AI Chat"],
)
async def upload_chat_attachment(
    request: Request,
    thread_id: str,
) -> Dict[str, Any]:
    """Upload a single file to a chat thread (Slice B — general file persistence).

    Files are owned by the thread (``owner_kind="chat"``), not filed to any
    entry. The frontend sends the returned ``attachment.id`` on the next
    ``SendMessageRequest.attachment_ids`` so the turn's context note can
    reference it (Task B3).
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    thread = await chat_threads_get_thread(thread_id)
    if thread is None or thread.user_id != user_id:
        raise ResourceNotFoundError(message="Thread not found")

    files = await _read_multipart_files(request, "file")
    if not files:
        raise BadRequestError(message="Multipart form must include a 'file' part.")

    result = await _persist_uploaded_chat_file(
        thread=thread,
        user_id=user_id,
        file=files[0],
    )
    return {**result, "message": "Attachment uploaded successfully"}


@endpoint(
    "/entries/{entry_id}/attachments/url",
    methods=["POST"],
    auth=True,
    tags=["Attachments"],
)
async def create_url_attachment(
    request: Request,
    entry_id: str,
    url: str,
    label: str = "",
) -> Dict[str, Any]:
    """Create an external URL attachment on an entry."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.update",
        resource=Resource(kind="entry", id=entry_id, scope=f"entry:{entry_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(
            message="You do not have permission to add attachments to this entry"
        )
    entry = await Entry.get(entry_id)
    if not entry:
        raise ResourceNotFoundError(message="Entry not found")

    raw_url = str(url or "").strip()
    await validate_public_http_url(raw_url)
    parsed = urlparse(raw_url)

    fallback_name = (parsed.netloc + (parsed.path or "")).strip("/") or parsed.netloc
    attachment = await Attachment.create(
        filename=label.strip() or fallback_name[:200],
        mime_type="text/uri-list",
        size=0,
        storage_key=f"attachments/{entry_id}/url/{datetime.now().timestamp()}",
        source_type="url",
        external_url=parsed.geturl(),
        uploaded_by=user_id,
        # URL attachments skip the scanner + extractor pipeline; mark
        # status explicitly so the UI knows the state is deliberate.
        scan_status="skipped",
        scan_engine="n/a",
        scan_message="URL attachments are not scanned.",
        metadata_status="skipped",
        created_at=utc_now_iso(),
    )
    await entry.connect(
        attachment,
        edge=HAS_ATTACHMENT,
        attached_at=utc_now_iso(),
        attached_by=user_id,
    )
    if attachment.id not in entry.attachment_ids:
        entry.attachment_ids.append(attachment.id)
        await entry.save()

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="attachment.create",
        resource_type="Attachment",
        resource_id=attachment.id,
        before=None,
        after=await export_node(attachment),
        scope=f"track:{entry.track_id or ''}",
    )

    return {
        "attachment": await export_node(attachment),
        "message": "URL attachment created successfully",
    }


@endpoint(
    "/entries/{entry_id}/attachments", methods=["GET"], auth=True, tags=["Attachments"]
)
async def list_entry_attachments(
    request: Request,
    entry_id: str,
) -> Dict[str, Any]:
    """List attachments for an entry (blocked attachments are filtered out)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.read",
        resource=Resource(kind="entry", id=entry_id, scope=f"entry:{entry_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(
            message="You do not have permission to view this entry's attachments"
        )
    entry = await Entry.get(entry_id)
    if not entry:
        raise ResourceNotFoundError(message="Entry not found")

    # ``entry.nodes(edge=...)`` returns every neighbour reachable via
    # the named edge — but in practice the traversal can surface
    # non-Attachment nodes when foreign edges share the registry (e.g.
    # the User author reachable via the same outgoing walk). Filter
    # to Attachment instances before dereferencing attachment-specific
    # fields so we don't crash on heterogeneous results.
    # Bound via nodes_page (jvspatial 0.0.18) — wire shape unchanged.
    neighbours, _next = await entry.nodes_page(
        edge=["HAS_ATTACHMENT"],
        direction="out",
        sort=[("context.created_at", -1), ("id", -1)],
        limit=500,
    )
    attachments = [a for a in neighbours if isinstance(a, Attachment)]
    visible = [a for a in attachments if is_visible_to_user(a)]
    # Newest first, tie-broken by id.
    #
    # ``entry.nodes(...)`` returns graph-traversal order, which is not a
    # defined order at all: the same four attachments came back in an order
    # matching neither ascending nor descending ``created_at``, and it can
    # differ between loads. That is visible twice over — the list reshuffles,
    # and the entry's hero preview is "the first image attachment", so which
    # image represents the entry could change without anyone touching it.
    # The id tie-break keeps the order total when timestamps collide (a
    # multi-file upload writes them in the same millisecond).
    visible.sort(
        key=lambda a: (str(getattr(a, "created_at", "") or ""), str(a.id)),
        reverse=True,
    )
    items: List[Dict[str, Any]] = []
    for a in visible:
        item = await export_node(a)
        await enrich_attachment_export(item, a)
        items.append(item)
    return {"attachments": items, "total": len(items), "entry_id": entry_id}


@endpoint(
    "/attachments/{attachment_id}", methods=["GET"], auth=True, tags=["Attachments"]
)
async def download_attachment(
    request: Request,
    attachment_id: str,
) -> Dict[str, Any]:
    """Get attachment metadata + download URL."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    attachment = await Attachment.get(attachment_id)
    if not attachment or not is_visible_to_user(attachment):
        raise ResourceNotFoundError(message="Attachment not found")

    entries = await attachment.nodes(edge=["HAS_ATTACHMENT"], direction="in")
    if not entries:
        raise ResourceNotFoundError(message="Attachment not linked to any entry")

    entry = entries[0]
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.read",
        resource=Resource(
            kind="entry",
            id=entry.id,
            scope=f"track:{entry.track_id or ''}",
        ),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(
            message="You do not have permission to access this attachment"
        )

    item = await export_node(attachment)
    await enrich_attachment_export(item, attachment)

    return {
        "attachment": item,
        "download_url": item.get("download_url"),
        "thumb_url": item.get("thumb_url"),
        "preview_url": item.get("preview_url"),
    }


@endpoint(
    "/attachments/{attachment_id}/download",
    methods=["GET"],
    auth=True,
    tags=["Attachments"],
)
async def stream_attachment(
    request: Request,
    attachment_id: str,
):
    """Stream/download attachment content for authorized users."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    attachment = await Attachment.get(attachment_id)
    if not attachment or not is_visible_to_user(attachment):
        raise ResourceNotFoundError(message="Attachment not found")

    entries = await attachment.nodes(edge=["HAS_ATTACHMENT"], direction="in")
    if not entries:
        raise ResourceNotFoundError(message="Attachment not linked to any entry")
    entry = entries[0]
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.read",
        resource=Resource(
            kind="entry",
            id=entry.id,
            scope=f"track:{entry.track_id or ''}",
        ),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(
            message="You do not have permission to access this attachment"
        )

    if attachment.source_type == "url":
        if not attachment.external_url:
            raise ResourceNotFoundError(message="Attachment URL is missing")
        return RedirectResponse(url=attachment.external_url)
    if not attachment.storage_key:
        raise ResourceNotFoundError(message="Attachment file is missing")

    storage = get_attachment_storage_service()
    blob = await storage.read_attachment(attachment.storage_key)
    if blob is None:
        raise ResourceNotFoundError(message="Attachment file not found in storage")
    meta = await storage.get_metadata(attachment.storage_key) or {}
    media_type = (
        attachment.mime_type
        or str(meta.get("content_type") or "").strip()
        or "application/octet-stream"
    )
    filename = attachment.filename or "attachment"

    headers = {
        "Content-Disposition": _content_disposition("attachment", filename),
    }
    return StreamingResponse(iter([blob]), media_type=media_type, headers=headers)


@endpoint(
    "/attachments/{attachment_id}/reprocess",
    methods=["POST"],
    auth=True,
    tags=["Attachments"],
)
async def reprocess_attachment_metadata(
    request: Request,
    attachment_id: str,
) -> Dict[str, Any]:
    """Re-run metadata extraction for an attachment.

    Idempotent: overwrites the existing metadata result. Useful when:
        - The metadata-extractor version has been bumped.
        - A previous extraction failed and the underlying library has
          since been installed (e.g. libmagic / pdfplumber).
        - An admin needs to refresh stale metadata.

    Permission: caller must be able to *edit* the parent entry, since
    re-extraction modifies the attachment record.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    attachment = await Attachment.get(attachment_id)
    if not attachment:
        raise ResourceNotFoundError(message="Attachment not found")
    entries = await attachment.nodes(edge=["HAS_ATTACHMENT"], direction="in")
    if not entries:
        raise ResourceNotFoundError(message="Attachment not linked to any entry")
    entry = entries[0]
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.update",
        resource=Resource(
            kind="entry",
            id=entry.id,
            scope=f"track:{entry.track_id or ''}",
        ),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(
            message="You do not have permission to reprocess this attachment"
        )

    service = get_metadata_extraction_service()
    updated = await service.extract_now(attachment_id)
    return {
        "attachment": await export_node(updated),
        "message": "Attachment metadata reprocessed",
    }


@endpoint(
    "/attachments/{attachment_id}/thumb",
    methods=["GET"],
    auth=True,
    tags=["Attachments"],
)
async def stream_attachment_thumbnail(
    request: Request,
    attachment_id: str,
):
    """Stream the cached image thumbnail for an attachment.

    Returns 404 when no thumbnail exists (non-image, scan-blocked,
    Pillow unavailable at upload time). The caller can fall back to
    the original image — for image attachments the original is itself
    browser-displayable.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    attachment = await Attachment.get(attachment_id)
    if not attachment or not is_visible_to_user(attachment):
        raise ResourceNotFoundError(message="Attachment not found")
    entries = await attachment.nodes(edge=["HAS_ATTACHMENT"], direction="in")
    if not entries:
        raise ResourceNotFoundError(message="Attachment not linked to any entry")
    entry = entries[0]
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.read",
        resource=Resource(
            kind="entry",
            id=entry.id,
            scope=f"track:{entry.track_id or ''}",
        ),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(
            message="You do not have permission to access this attachment"
        )

    if not attachment.thumb_storage_key:
        raise ResourceNotFoundError(message="Thumbnail not available")

    storage = get_attachment_storage_service()
    blob = await storage.read_attachment(attachment.thumb_storage_key)
    if blob is None:
        raise ResourceNotFoundError(message="Thumbnail not found in storage")

    # Long browser cache is safe because the sibling key is unique per
    # attachment_id; replacing the original creates a new attachment
    # with a new key.
    headers = {
        "Cache-Control": "private, max-age=86400, immutable",
    }
    return StreamingResponse(iter([blob]), media_type="image/jpeg", headers=headers)


@endpoint(
    "/attachments/{attachment_id}/preview",
    methods=["GET"],
    auth=True,
    tags=["Attachments"],
)
async def stream_attachment_preview(
    request: Request,
    attachment_id: str,
):
    """Stream a server-rendered PDF preview for office documents.

    On first request the cached preview is generated via headless
    LibreOffice and persisted at a sibling storage key. Subsequent
    requests stream the cached PDF directly.

    Returns:
        - 200 with ``application/pdf`` on success.
        - 404 when the attachment isn't previewable (wrong MIME).
        - 503 when LibreOffice isn't installed or conversion failed.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    attachment = await Attachment.get(attachment_id)
    if not attachment or not is_visible_to_user(attachment):
        raise ResourceNotFoundError(message="Attachment not found")
    entries = await attachment.nodes(edge=["HAS_ATTACHMENT"], direction="in")
    if not entries:
        raise ResourceNotFoundError(message="Attachment not linked to any entry")
    entry = entries[0]
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.read",
        resource=Resource(
            kind="entry",
            id=entry.id,
            scope=f"track:{entry.track_id or ''}",
        ),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(
            message="You do not have permission to access this attachment"
        )

    if not can_preview(attachment.mime_type):
        raise ResourceNotFoundError(
            message="No server-rendered preview available for this file type."
        )

    try:
        preview_key = await ensure_preview(attachment, entry_id=entry.id)
    except PreviewUnavailableError as e:
        # 503 (Service Unavailable) signals "the feature is real, it's
        # just not operational right now" — frontends should fall back
        # to the download path. BadRequest wouldn't be accurate.
        raise BadRequestError(message=str(e))

    if not preview_key:
        raise ResourceNotFoundError(message="Preview not available")

    storage = get_attachment_storage_service()
    blob = await storage.read_attachment(preview_key)
    if blob is None:
        raise ResourceNotFoundError(message="Preview not found in storage")

    preview_name = f"{attachment.filename or 'preview'}.pdf"
    headers = {
        "Cache-Control": "private, max-age=3600",
        "Content-Disposition": _content_disposition("inline", preview_name),
    }
    return StreamingResponse(
        iter([blob]), media_type="application/pdf", headers=headers
    )


@endpoint(
    "/entries/{entry_id}/uploads",
    methods=["POST"],
    auth=True,
    tags=["Attachments"],
)
async def start_chunked_upload(
    request: Request,
    entry_id: str,
    filename: str,
    total_bytes: int,
    mime_type: str = "",
    chunk_size: Optional[int] = None,
) -> Dict[str, Any]:
    """Initiate a resumable chunked upload session.

    Plan 03 — Phase 6. Returns a session id the client uses to PUT
    chunks via ``/uploads/{session_id}/chunks/{index}``. The client
    can drop and reconnect freely; the session survives across
    requests until either ``complete`` or ``cancel`` (or it expires).
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.update",
        resource=Resource(kind="entry", id=entry_id, scope=f"entry:{entry_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(
            message="You do not have permission to add attachments to this entry"
        )
    entry = await Entry.get(entry_id)
    if not entry:
        raise ResourceNotFoundError(message="Entry not found")

    session = await chunked_start_session(
        entry=entry,
        user_id=user_id,
        filename=filename,
        mime_type=mime_type,
        total_bytes=total_bytes,
        chunk_size=chunk_size,
    )
    return {"session": chunked_session_to_dict(session)}


@endpoint(
    "/uploads/{session_id}",
    methods=["GET"],
    auth=True,
    tags=["Attachments"],
)
async def get_chunked_upload_session(
    request: Request,
    session_id: str,
) -> Dict[str, Any]:
    """Return the current state of an upload session.

    Used by clients to resume after a disconnect — the response's
    ``received_bytes`` and ``next_chunk_index`` tell the client where
    to pick up from.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    session = await chunked_get_session(session_id)
    if not session:
        raise ResourceNotFoundError(message="Upload session not found")
    if session.uploaded_by and session.uploaded_by != user_id:
        raise InsufficientPermissionsError(message="Access denied")
    return {"session": chunked_session_to_dict(session)}


@endpoint(
    "/uploads/{session_id}/chunks/{chunk_index}",
    methods=["PUT"],
    auth=True,
    tags=["Attachments"],
)
async def append_chunked_upload_part(
    request: Request,
    session_id: str,
    chunk_index: int,
) -> Dict[str, Any]:
    """Persist a single chunk of a resumable upload.

    Body: raw bytes (Content-Type ``application/octet-stream``). We
    deliberately don't accept multipart here — the chunked path is for
    large files where the extra envelope overhead matters.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    session = await chunked_get_session(session_id)
    if not session:
        raise ResourceNotFoundError(message="Upload session not found")
    if session.uploaded_by and session.uploaded_by != user_id:
        raise InsufficientPermissionsError(message="Access denied")

    body = await request.body()
    if not body:
        raise BadRequestError(message="Chunk body required")
    session = await chunked_append_chunk(
        session=session, chunk_index=int(chunk_index), chunk_bytes=body
    )
    return {"session": chunked_session_to_dict(session)}


@endpoint(
    "/uploads/{session_id}/complete",
    methods=["POST"],
    auth=True,
    tags=["Attachments"],
)
async def complete_chunked_upload(
    request: Request,
    session_id: str,
) -> Dict[str, Any]:
    """Finalize a resumable upload into an Attachment.

    Concatenates staged chunks, sniffs/scans/dedupes, creates the
    Attachment node, binds it to the entry, and schedules metadata
    extraction. Staged chunk blobs are deleted on success.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    session = await chunked_get_session(session_id)
    if not session:
        raise ResourceNotFoundError(message="Upload session not found")
    if session.uploaded_by and session.uploaded_by != user_id:
        raise InsufficientPermissionsError(message="Access denied")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.update",
        resource=Resource(
            kind="entry",
            id=session.entry_id,
            scope=f"entry:{session.entry_id}",
        ),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(
            message="You do not have permission to add attachments to this entry"
        )

    entry = await Entry.get(session.entry_id)
    if not entry:
        raise ResourceNotFoundError(message="Entry not found")

    assembled, sha256_hex = await chunked_assemble_session(session)
    try:
        result = await _persist_assembled_content(
            entry=entry,
            user_id=user_id,
            filename=session.filename,
            content=assembled,
            declared_mime=session.mime_type,
            sha256_hex=sha256_hex,
        )
    except Exception:
        # Leave the session reachable for diagnostics; clear staged
        # chunks so they don't linger in storage.
        with contextlib.suppress(Exception):
            await chunked_clear_chunks(session)
        raise

    # Mark complete + sweep staged chunks before responding.
    session.status = "complete"
    session.received_bytes = session.total_bytes
    session.content_hash = sha256_hex
    await session.save()
    await chunked_clear_chunks(session)

    return {
        **result,
        "session": chunked_session_to_dict(session),
        "message": "Attachment uploaded successfully",
    }


@endpoint(
    "/uploads/{session_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Attachments"],
)
async def cancel_chunked_upload(
    request: Request,
    session_id: str,
) -> Dict[str, Any]:
    """Cancel an in-progress upload and reclaim staged storage."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    session = await chunked_get_session(session_id)
    if not session:
        raise ResourceNotFoundError(message="Upload session not found")
    if session.uploaded_by and session.uploaded_by != user_id:
        raise InsufficientPermissionsError(message="Access denied")
    await chunked_cancel_session(session)
    return {"session": chunked_session_to_dict(session), "message": "Upload cancelled"}


async def _persist_assembled_content(
    *,
    entry: Entry,
    user_id: str,
    filename: str,
    content: bytes,
    declared_mime: str,
    sha256_hex: str,
) -> Dict[str, Any]:
    """Push already-assembled bytes through the full pipeline.

    Phase 6 hand-off: take assembled chunked-upload bytes through the
    same sniff / scan / dedup / store / metadata pipeline.
    This is the chunked-upload counterpart of ``_persist_uploaded_file``.
    Logic is intentionally near-identical so behaviour stays consistent
    regardless of how the bytes arrived — sniffing still wins, scanner
    still runs, dedup still fires, metadata still dispatches.
    """
    head_bytes = content[:4096]
    mime_type = _resolve_effective_mime(
        head_bytes=head_bytes,
        content_type_claim=declared_mime,
        filename=filename,
    )
    file_size = len(content)

    existing = await _find_duplicate_hash_on_entry(entry, sha256_hex)
    if existing is not None:
        raise ResourceConflictError(
            message=("An attachment with identical content is already on this entry."),
        )

    # Quota gate again — the session-init check used the declared
    # total. Verifying at finalize covers the case where another
    # upload pushed the org over while this one was streaming.
    over_quota = await check_quota_for_entry(entry, file_size)
    if over_quota is not None:
        raise BadRequestError(
            message=(
                "Workspace storage quota exceeded "
                f"({over_quota.bytes_used} / {over_quota.quota_bytes} bytes)."
            ),
        )

    attachment = await Attachment.create(
        filename=filename,
        mime_type=mime_type,
        size=file_size,
        storage_key="",
        source_type="file",
        external_url="",
        uploaded_by=user_id,
        content_hash=sha256_hex,
        scan_status="pending",
        metadata_status="pending",
        created_at=utc_now_iso(),
    )
    storage = get_attachment_storage_service()
    try:
        result = await storage.save_attachment(
            entry_id=entry.id,
            attachment_id=attachment.id,
            filename=filename,
            content=content,
            mime_type=mime_type,
            metadata={
                "entry_id": entry.id,
                "attachment_id": attachment.id,
                "uploaded_by": user_id,
                "sha256": sha256_hex,
                "via": "chunked",
            },
        )
    except Exception:
        await attachment.delete()
        raise

    attachment.storage_key = str(result.get("path") or "")
    await attachment.save()

    scanner = get_attachment_scanner()
    try:
        verdict = await scanner.scan(
            content=content, mime_type=mime_type, filename=filename
        )
    except Exception as e:  # noqa: BLE001
        attachment.scan_status = "failed"
        attachment.scan_engine = scanner.name
        attachment.scan_message = f"scanner crashed: {e}"
    else:
        attachment.scan_status = verdict.status
        attachment.scan_engine = verdict.engine
        attachment.scan_message = verdict.message
        if verdict.is_blocked:
            with contextlib.suppress(Exception):
                await storage.delete_attachment(attachment.storage_key)
            attachment.storage_key = ""
    await attachment.save()

    if attachment.scan_status != SCAN_STATUS_BLOCKED:
        try:
            thumb_key = await ensure_thumbnail(
                attachment, entry_id=entry.id, content=content, storage=storage
            )
            if thumb_key:
                await attachment.save()
        except Exception:  # noqa: BLE001
            pass

    await entry.connect(
        attachment,
        edge=HAS_ATTACHMENT,
        attached_at=utc_now_iso(),
        attached_by=user_id,
    )
    if attachment.id not in entry.attachment_ids:
        entry.attachment_ids.append(attachment.id)
        await entry.save()

    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="attachment.create",
        resource_type="Attachment",
        resource_id=attachment.id,
        before=None,
        after=await export_node(attachment),
        scope=f"track:{entry.track_id or ''}",
    )

    if attachment.scan_status != SCAN_STATUS_BLOCKED:
        get_metadata_extraction_service().schedule_extraction(attachment.id)
        if attachment.storage_key:
            await increment_for_entry(entry, file_size)

    return {"attachment": await export_node(attachment)}


def _quote_disposition_filename(filename: str) -> str:
    """Escape a filename for use inside a quoted Content-Disposition token."""
    return filename.replace("\\", "\\\\").replace('"', '\\"')


def _ascii_disposition_fallback(filename: str) -> str:
    """Return a conservative ASCII filename for legacy Content-Disposition clients."""
    chars: List[str] = []
    for ch in filename:
        if ord(ch) < 128 and ch not in "\r\n":
            chars.append(ch)
        else:
            chars.append("_")
    return "".join(chars).strip("._") or "attachment"


def _content_disposition(disposition: str, filename: str) -> str:
    """Build a latin-1-safe Content-Disposition header value.

    HTTP response headers must be latin-1 encodable. Filenames copied from
    office documents or localized filesystems may contain characters outside
    that range (e.g. U+202F). When that happens we emit an ASCII fallback
    ``filename`` token plus an RFC 5987 ``filename*`` UTF-8 parameter.
    """
    name = (filename or "").strip() or "attachment"
    escaped = _quote_disposition_filename(name)
    try:
        name.encode("latin-1")
        return f'{disposition}; filename="{escaped}"'
    except UnicodeEncodeError:
        fallback = _quote_disposition_filename(_ascii_disposition_fallback(name))
        encoded = quote(name, safe="")
        return f'{disposition}; filename="{fallback}"; ' f"filename*=UTF-8''{encoded}"


def _safe_zip_member_name(filename: str, fallback: str) -> str:
    """Return a zip-safe member filename.

    Strip path separators (defence-in-depth even though attachment
    filenames already go through ``_sanitize_filename`` upstream), and
    fall back to a synthetic name when the field is empty.
    """
    name = (filename or "").replace("/", "_").replace("\\", "_").strip()
    return name or fallback


@endpoint(
    "/entries/{entry_id}/attachments/download-all",
    methods=["GET"],
    auth=True,
    tags=["Attachments"],
)
async def download_entry_attachments_zip(
    request: Request,
    entry_id: str,
):
    """Stream a single ZIP containing every visible file attachment.

    Plan 03 — Phase 5. Useful when a user wants all the documents
    associated with an entry without clicking download N times. URL
    attachments are skipped — they have no bytes to bundle.

    The zip is built in-memory for simplicity (per-entry attachment
    sets are bounded by the same caps as the upload path: ~10 files,
    500 MB each). Streaming for truly large sets is a future
    enhancement; ``zipfile`` doesn't natively stream, so we'd need to
    switch to a generator-friendly implementation when needed.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.read",
        resource=Resource(kind="entry", id=entry_id, scope=f"entry:{entry_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(
            message="You do not have permission to view this entry's attachments"
        )
    entry = await Entry.get(entry_id)
    if not entry:
        raise ResourceNotFoundError(message="Entry not found")

    attachments = await entry.nodes(edge=["HAS_ATTACHMENT"], direction="out")
    files = [
        a
        for a in attachments
        if isinstance(a, Attachment)
        and a.source_type == "file"
        and is_visible_to_user(a)
        and a.storage_key
    ]
    if not files:
        raise ResourceNotFoundError(
            message="This entry has no downloadable file attachments."
        )

    storage = get_attachment_storage_service()
    buf = io.BytesIO()
    used_names: Dict[str, int] = {}
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for idx, att in enumerate(files):
            blob = await storage.read_attachment(att.storage_key)
            if blob is None:
                continue
            member = _safe_zip_member_name(att.filename, f"attachment-{idx}")
            # Disambiguate name collisions on entries with multiple
            # files sharing a filename (rare but possible).
            count = used_names.get(member, 0)
            if count:
                stem, dot, ext = member.rpartition(".")
                if dot:
                    member = f"{stem}-{count}.{ext}"
                else:
                    member = f"{member}-{count}"
            used_names[att.filename or member] = count + 1
            zf.writestr(member, blob)
    buf.seek(0)

    zip_name = (
        f"entry-{entry_id}-attachments.zip"
        if not (entry.title or "").strip()
        else f"{entry.title.strip().replace(' ', '_')[:64]}-attachments.zip"
    )
    headers = {
        "Content-Disposition": _content_disposition("attachment", zip_name),
    }
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="application/zip", headers=headers
    )


@endpoint(
    "/attachments/{attachment_id}", methods=["DELETE"], auth=True, tags=["Attachments"]
)
async def delete_attachment(
    request: Request,
    attachment_id: str,
) -> Dict[str, Any]:
    """Delete an attachment."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    attachment = await Attachment.get(attachment_id)
    if not attachment:
        raise ResourceNotFoundError(message="Attachment not found")

    entries = await attachment.nodes(edge=["HAS_ATTACHMENT"], direction="in")
    if not entries:
        raise ResourceNotFoundError(message="Attachment not linked to any entry")

    entry = entries[0]

    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.update",
        resource=Resource(
            kind="entry",
            id=entry.id,
            scope=f"track:{entry.track_id or ''}",
        ),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(
            message="You do not have permission to delete this attachment"
        )

    prior_snapshot = await export_node(attachment)  # D-03 before-snapshot
    track_id_for_scope = entry.track_id or ""

    if attachment.source_type == "file":
        storage = get_attachment_storage_service()
        # Best-effort cleanup of the original + all sibling artefacts
        # (thumbnail, preview). Storage failures don't block the node
        # delete — orphan blobs are recoverable via the
        # storage-reconcile job.
        for key in (
            attachment.storage_key,
            attachment.thumb_storage_key,
            attachment.preview_storage_key,
        ):
            if not key:
                continue
            with contextlib.suppress(Exception):
                await storage.delete_attachment(key)
        # Phase 5: release the counter once the bytes are gone. Only
        # bytes that actually landed (non-blocked) were ever counted,
        # and decrement floors at zero so we can never drive the
        # counter negative on transient miscounts.
        if attachment.scan_status != SCAN_STATUS_BLOCKED and attachment.size:
            await decrement_for_entry(entry, int(attachment.size or 0))

    if attachment_id in (entry.attachment_ids or []):
        entry.attachment_ids = [
            aid for aid in entry.attachment_ids if aid != attachment_id
        ]
        await entry.save()
    await attachment.delete()

    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="attachment.delete",
        resource_type="Attachment",
        resource_id=attachment_id,
        before=prior_snapshot,
        after=None,
        scope=f"track:{track_id_for_scope}",
    )

    return {
        "message": "Attachment deleted successfully",
        "deleted_attachment_id": attachment_id,
    }
