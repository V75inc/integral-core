"""Persist chat-owned uploads using the shared attachment pipeline.

The caller resolves the owning thread and authorizes its workspace. The same
service can be used by HTTP chat uploads and other trusted host integrations.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any, Dict, Optional

from fastapi import UploadFile

from app.api.errors import BadRequestError, ResourceConflictError, ResourceNotFoundError
from app.api.utils import export_node
from app.config import settings
from app.models.edges import HAS_ATTACHMENT
from app.models.nodes import Attachment, ChatThread
from app.services.attachment_metadata import get_metadata_extraction_service
from app.services.attachment_scanner import SCAN_STATUS_BLOCKED, get_attachment_scanner
from app.services.attachment_storage import get_attachment_storage_service
from app.services.attachment_thumbnails import ensure_thumbnail
from app.services.attachment_upload_shared import (
    read_upload_streaming as _read_upload_streaming,
)
from app.services.attachment_upload_shared import (
    resolve_effective_mime as _resolve_effective_mime,
)
from app.services.change_event import emit_change_event
from app.services.workspace_storage_usage import check_quota as check_workspace_quota
from app.services.workspace_storage_usage import increment as increment_workspace_usage
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


async def _reuse_duplicate(
    attachment: Attachment, *, schedule_metadata: bool = True
) -> Dict[str, Any]:
    """Resume pending metadata after a committed upload's response was lost."""
    if schedule_metadata and attachment.metadata_status == "pending":
        get_metadata_extraction_service().schedule_extraction(attachment.id)
    return {"attachment": await export_node(attachment), "deduplicated": True}


async def _fresh_attachment(candidate: Attachment) -> Optional[Attachment]:
    """Read the persisted owner and scan state, never cached hydration."""
    from jvspatial.core.context import get_default_context

    from app.services.authority_facts import primary_node_record

    graph = get_default_context()
    row = await primary_node_record(graph.database, Attachment, candidate.id)
    if not row:
        return None
    return await graph._deserialize_entity(Attachment, row)


async def _find_duplicate_hash_on_thread(
    thread: ChatThread, content_hash: str
) -> Optional[Attachment]:
    """Return a reusable, unfiled chat attachment matching ``content_hash``.

    A filed attachment retains its chat edge for provenance but is now governed
    by its Entry. Reusing it as a fresh upload would return a source that the
    filing path correctly rejects, and could bypass the Entry's access model.
    Blocked attachments or ones without a storage key are not reusable either.
    """
    if not content_hash:
        return None
    existing = await thread.nodes(edge=["HAS_ATTACHMENT"], direction="out")
    for att in existing:
        if isinstance(att, Attachment) and att.content_hash == content_hash:
            att = await _fresh_attachment(att)
        if (
            isinstance(att, Attachment)
            and getattr(att, "content_hash", "") == content_hash
            and getattr(att, "owner_kind", "entry") == "chat"
            and getattr(att, "scan_status", "") in {"clean", "skipped"}
            and bool(getattr(att, "storage_key", ""))
        ):
            return att
    return None


async def persist_chat_upload(
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

    Callers must resolve the live thread and authorize its workspace. The
    ownership check here is a second guard, not a substitute for admission.
    """
    if not user_id or thread.user_id != user_id:
        raise ResourceNotFoundError(message="Thread not found")
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
        return await _reuse_duplicate(existing)

    over_quota = await check_workspace_quota(thread.workspace_id, file_size)
    if over_quota is not None:
        raise BadRequestError(
            message=(
                "Workspace storage quota exceeded "
                f"({over_quota.bytes_used} / {over_quota.quota_bytes} bytes). "
                "Delete files or contact an admin to raise the quota."
            ),
        )

    storage = get_attachment_storage_service()
    from jvspatial.core.context import get_default_context

    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
        postgres_graph_transaction,
    )

    caller_context = get_default_context()
    transactional = graph_transaction_available()
    transaction = (
        postgres_graph_transaction() if transactional else contextlib.nullcontext()
    )
    attachment = None
    try:
        async with transaction as database:
            if transactional:
                # The parent lock serializes competing registrations for the
                # same chat, without spanning blob storage or scanner I/O.
                if await database.lock_record("node", thread.id) is None:
                    raise ResourceNotFoundError(message="Thread not found")
            current_thread = await ChatThread.get(thread.id)
            if (
                current_thread is None
                or current_thread.user_id != user_id
                or current_thread.workspace_id != thread.workspace_id
            ):
                raise ResourceNotFoundError(message="Thread not found")
            # The optimistic check above cannot see another in-flight upload.
            # Recheck under the parent lock before making another graph node.
            candidates = await current_thread.nodes(
                edge=[HAS_ATTACHMENT], node=["Attachment"], direction="out"
            )
            for candidate in candidates:
                if (
                    isinstance(candidate, Attachment)
                    and candidate.content_hash == sha256_hex
                ):
                    candidate = await _fresh_attachment(candidate)
                if (
                    isinstance(candidate, Attachment)
                    and candidate.owner_kind == "chat"
                    and candidate.content_hash == sha256_hex
                ):
                    if candidate.scan_status == "pending":
                        raise ResourceConflictError(
                            message="This file is still being prepared. Try again shortly; "
                            "contact an administrator if it remains pending."
                        )
                    if (
                        candidate.scan_status in {"clean", "skipped"}
                        and candidate.storage_key
                    ):
                        return await _reuse_duplicate(
                            candidate, schedule_metadata=False
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
            await current_thread.connect(
                attachment,
                edge=HAS_ATTACHMENT,
                attached_at=utc_now_iso(),
                attached_by=user_id,
            )
    except BaseException:
        if not transactional and attachment is not None:
            with contextlib.suppress(Exception):
                await attachment.delete()
        raise
    await attachment.set_context(caller_context)
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
        with contextlib.suppress(Exception):
            await storage.delete_attachment(
                storage.build_storage_key(f"chat/{thread.id}", attachment.id, filename)
            )
        await attachment.delete()
        raise

    stored_key = str(result.get("path") or "")
    attachment.storage_key = stored_key
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
    if transactional:
        from app.services.chat_upload_finalization import finalize_chat_upload

        attachment, quota_rejected, event_outbox_id = await finalize_chat_upload(
            user_id=user_id,
            workspace_id=thread.workspace_id,
            thread_id=thread.id,
            attachment_id=attachment.id,
            content_hash=sha256_hex,
            size=file_size,
            storage_key=stored_key,
            scan_status=attachment.scan_status,
            scan_engine=attachment.scan_engine,
            scan_message=attachment.scan_message,
        )
        await attachment.set_context(caller_context)
        if quota_rejected:
            with contextlib.suppress(Exception):
                if await storage.delete_attachment(stored_key):
                    await caller_context.database.find_one_and_update(
                        "node",
                        {
                            "id": attachment.id,
                            "context.scan_status": "quota_rejected",
                            "context.storage_key": stored_key,
                        },
                        {"$set": {"context.storage_key": ""}},
                    )
            raise BadRequestError(message="Workspace storage quota exceeded.")
    else:
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
                await caller_context.database.find_one_and_update(
                    "node",
                    {
                        "id": attachment.id,
                        "entity": Attachment._entity_name(),
                        "context.content_hash": sha256_hex,
                        "context.storage_key": stored_key,
                    },
                    {"$set": {"context.thumb_storage_key": thumb_key}},
                )
                await caller_context._evict_from_cache(attachment.id)
                attachment = await _fresh_attachment(attachment) or attachment
        except Exception:  # noqa: BLE001
            pass

    if transactional:
        from app.services.app_operations.event_outbox import deliver_operation_event

        try:
            await deliver_operation_event(outbox_id=event_outbox_id)
        except Exception:  # noqa: BLE001
            logger.exception("Chat upload event awaits recovery")
    else:
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

    if (
        not transactional
        and attachment.scan_status != SCAN_STATUS_BLOCKED
        and attachment.storage_key
    ):
        await increment_workspace_usage(thread.workspace_id, file_size)

    return {"attachment": await export_node(attachment)}
