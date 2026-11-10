"""Revision-bound preparation receipts using jvspatial's native work claim.

This module handles only the durable control record and graph finalization.
Rendering and blob storage must happen outside its short transactions.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from jvspatial import claim_record, release_claim
from jvspatial.core.context import get_default_context

from app.api.errors import ResourceConflictError, ResourceNotFoundError
from app.models.harness_records import (
    HarnessDraftBlobAttempt,
    HarnessDraftPreparationRecord,
)
from app.services.app_operations.transaction_scope import (
    current_graph_transaction_database,
    graph_transaction_available,
    require_outside_graph_transaction,
)
from app.services.document_draft_access import (
    DocumentDraftSnapshot,
    require_current_document_draft,
)
from app.services.release_admission import release_admission

logger = logging.getLogger(__name__)

_work_context = ContextVar("document_preparation_work_context", default=None)


@contextmanager
def bind_preparation_work_context(context):
    """Bind trusted worker authority for each short preparation transaction."""
    token = _work_context.set(context)
    try:
        yield
    finally:
        _work_context.reset(token)


@asynccontextmanager
async def _preparation_admission(snapshot):
    context = _work_context.get()
    if context is None:
        async with release_admission(snapshot.user_id, snapshot.workspace_id):
            yield
        return
    if (
        context.principal_id != snapshot.user_id
        or context.workspace_id != snapshot.workspace_id
        or context.thread_id != snapshot.thread_id
    ):
        raise ResourceNotFoundError(message="Conversation not found")
    from app.agentive.services.work_items import authorized_work_item_effect

    # Lock the WorkItem before substrate authority and publication locks, as
    # for other worker effects. No rendering or storage I/O occurs here.
    async with authorized_work_item_effect(context):
        async with release_admission(snapshot.user_id, snapshot.workspace_id):
            yield


@dataclass(frozen=True)
class DraftPreparation:
    """A scoped claim or a previously prepared attachment, never both."""

    record_id: str
    source_digest: str
    binary_digest: str
    binary_size: int
    token: str = field(default="", repr=False)
    attachment_id: str = ""


@dataclass(frozen=True)
class DraftBlobAttempt:
    """A recorded, private blob; it is not an Attachment or a publication."""

    attempt_id: str
    storage_key: str = field(repr=False)
    filename: str
    mime_type: str
    scan_status: str
    scan_engine: str
    scan_message: str = field(repr=False)


async def prepare_document_draft(snapshot: DocumentDraftSnapshot) -> str:
    """Return one private chat attachment for the exact current draft revision.

    A committed receipt is replayed after a lost response. Interrupted blob
    writes retain distinct attempt records for later reconciliation.
    """
    from app.services.document_drafts import render_document_draft

    _require_transactional_store()
    require_outside_graph_transaction()
    await require_current_document_draft(snapshot)
    rendered = await asyncio.to_thread(
        render_document_draft,
        title=snapshot.title,
        body=snapshot.body,
        format=snapshot.format,
    )
    claim = await claim_draft_preparation(
        snapshot,
        binary_digest=hashlib.sha256(rendered.content).hexdigest(),
        binary_size=len(rendered.content),
    )
    if claim.attachment_id:
        await _schedule_pending_metadata(claim.attachment_id)
        return claim.attachment_id
    try:
        attempt = await stage_draft_preparation_blob(snapshot, claim)
        attachment_id = await publish_staged_draft(
            snapshot, claim, attempt_id=attempt.attempt_id
        )
        await _schedule_pending_metadata(attachment_id)
        return attachment_id
    except BaseException:
        # Native release is token-checked: a displaced worker cannot release a
        # successor's lease. If cancellation interrupts release, expiry still
        # permits later recovery; storage attempts remain recorded separately.
        try:
            await asyncio.shield(
                release_claim(
                    get_default_context().database,
                    "object",
                    claim.record_id,
                    claim.token,
                )
            )
        except (Exception, asyncio.CancelledError):  # Preserve the original failure.
            pass
        raise


async def _schedule_pending_metadata(attachment_id: str) -> None:
    """Request the existing extraction path after commit or receipt replay."""
    from app.models.nodes import Attachment
    from app.services.attachment_metadata import get_metadata_extraction_service
    from app.services.authority_facts import primary_node_record

    try:
        row = await primary_node_record(
            get_default_context().database, Attachment, attachment_id
        )
        if row and (row.get("context") or {}).get("metadata_status") == "pending":
            get_metadata_extraction_service().schedule_extraction(attachment_id)
    except Exception:  # A committed file remains available for later recovery.
        logger.exception("Could not schedule generated-file metadata extraction")


def _identity(snapshot: DocumentDraftSnapshot, digest: str, size: int) -> dict:
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or type(size) is not int or size < 1:
        raise ValueError("A binary digest and positive file size are required")
    return {
        "principal_id": snapshot.user_id,
        "workspace_id": snapshot.workspace_id,
        "thread_id": snapshot.thread_id,
        "source_digest": snapshot.source_digest,
        "binary_digest": digest,
        "binary_size": size,
    }


def _record_id(identity: dict) -> str:
    source = f'{identity["source_digest"]}\0{identity["binary_digest"]}\0{identity["binary_size"]}'
    return (
        "o.HarnessDraftPreparationRecord." + hashlib.sha256(source.encode()).hexdigest()
    )


def _require_transactional_store() -> None:
    if not graph_transaction_available():
        raise ResourceConflictError(
            message="Safe document preparation is unavailable on this storage adapter."
        )


async def _lock_current_claim(snapshot: DocumentDraftSnapshot, claim: DraftPreparation):
    """Lock the native claim and check its identity and lease in this transaction."""
    identity = _identity(snapshot, claim.binary_digest, claim.binary_size)
    if (
        not claim.token
        or claim.source_digest != snapshot.source_digest
        or claim.record_id != _record_id(identity)
    ):
        raise ResourceConflictError(message="Document preparation claim is invalid.")
    database = current_graph_transaction_database()
    row = await database.find_one_and_update(
        "object",
        {
            "id": claim.record_id,
            "entity": HarnessDraftPreparationRecord._entity_name(),
            "context.state": "pending",
            "_jv_claim": claim.token,
            "_jv_claim_until": {"$gt": time.time()},
            **{f"context.{key}": value for key, value in identity.items()},
        },
        {"$set": {}},
    )
    if row is None:
        raise ResourceConflictError(
            message="Document preparation expired or changed. Try again."
        )


async def stage_draft_preparation_blob(
    snapshot: DocumentDraftSnapshot, claim: DraftPreparation
) -> DraftBlobAttempt:
    """Record and write a unique private blob, then recheck the native claim.

    The returned attempt is internal: it is not yet a visible Attachment.
    A failed or displaced write remains recorded for future reconciliation.
    """
    from app.config import settings
    from app.services.attachment_scanner import get_attachment_scanner
    from app.services.attachment_storage import get_attachment_storage_service
    from app.services.document_drafts import render_document_draft
    from app.utils.time import utc_now_iso

    _require_transactional_store()
    require_outside_graph_transaction()
    await require_current_document_draft(snapshot)
    rendered = await asyncio.to_thread(
        render_document_draft,
        title=snapshot.title,
        body=snapshot.body,
        format=snapshot.format,
    )
    content = rendered.content
    if (
        len(content) > settings.ATTACHMENT_MAX_UPLOAD_BYTES
        or len(content) != claim.binary_size
        or hashlib.sha256(content).hexdigest() != claim.binary_digest
    ):
        raise ResourceConflictError(
            message="Rendered document changed or is too large."
        )
    filename = f"draft.{rendered.extension}"
    verdict = await get_attachment_scanner().scan(
        content=content, mime_type=rendered.mime_type, filename=filename
    )
    if verdict.status not in {"clean", "skipped"}:
        raise ResourceConflictError(
            message="The document could not be safely prepared."
        )

    storage = get_attachment_storage_service()
    attempt_key = uuid.uuid4().hex
    entry_id = f"draft-preparation/{snapshot.thread_id}"
    storage_key = storage.build_storage_key(entry_id, attempt_key, filename)
    attempt_id = f"o.HarnessDraftBlobAttempt.{attempt_key}"
    async with _preparation_admission(snapshot):
        await require_current_document_draft(snapshot)
        await _lock_current_claim(snapshot, claim)
        _, created = await HarnessDraftBlobAttempt.create_if_absent(
            id=attempt_id,
            preparation_id=claim.record_id,
            workspace_id=snapshot.workspace_id,
            thread_id=snapshot.thread_id,
            claim_digest=hashlib.sha256(claim.token.encode()).hexdigest(),
            storage_key=storage_key,
            created_at=utc_now_iso(),
        )
        if not created:
            raise ResourceConflictError(
                message="Document preparation attempt collided."
            )

    result = await storage.save_attachment(
        entry_id=entry_id,
        attachment_id=attempt_key,
        filename=filename,
        content=content,
        mime_type=rendered.mime_type,
        metadata={"sha256": claim.binary_digest, "attempt_id": attempt_id},
    )
    stored = await storage.read_attachment(storage_key)
    if (
        result.get("path") != storage_key
        or stored is None
        or len(stored) != claim.binary_size
        or hashlib.sha256(stored).hexdigest() != claim.binary_digest
    ):
        raise ResourceConflictError(
            message="Prepared document bytes could not be verified."
        )
    async with _preparation_admission(snapshot):
        await require_current_document_draft(snapshot)
        await _lock_current_claim(snapshot, claim)
    return DraftBlobAttempt(
        attempt_id=attempt_id,
        storage_key=storage_key,
        filename=filename,
        mime_type=rendered.mime_type,
        scan_status=verdict.status,
        scan_engine=verdict.engine,
        scan_message=verdict.message,
    )


async def publish_staged_draft(
    snapshot: DocumentDraftSnapshot, claim: DraftPreparation, *, attempt_id: str
) -> str:
    """Commit one verified attempt as a private, chat-owned Attachment.

    This is an internal preparation boundary, not Entry filing. Blob reads and
    scanning happen before the short transaction; the receipt and chat edge
    commit together while the native claim remains current.
    """
    from app.api.errors import BadRequestError
    from app.config import settings
    from app.models.edges import HAS_ATTACHMENT
    from app.models.nodes import Attachment, ChatThread, Workspace
    from app.services.app_operations.event_outbox import insert_revision_event
    from app.services.attachment_scanner import get_attachment_scanner
    from app.services.attachment_storage import get_attachment_storage_service
    from app.services.document_render import extension_for, mime_for
    from app.utils.time import utc_now_iso

    _require_transactional_store()
    require_outside_graph_transaction()
    match = re.fullmatch(r"o\.HarnessDraftBlobAttempt\.([0-9a-f]{32})", attempt_id)
    if not match:
        raise ResourceNotFoundError(message="Document preparation attempt not found")
    await require_current_document_draft(snapshot)
    storage = get_attachment_storage_service()
    filename = f"draft.{extension_for(snapshot.format)}"
    mime_type = mime_for(snapshot.format)
    storage_key = storage.build_storage_key(
        f"draft-preparation/{snapshot.thread_id}", match.group(1), filename
    )
    attempt_identity = {
        "preparation_id": claim.record_id,
        "workspace_id": snapshot.workspace_id,
        "thread_id": snapshot.thread_id,
        "claim_digest": hashlib.sha256(claim.token.encode()).hexdigest(),
        "storage_key": storage_key,
        "state": "reserved",
    }
    row = await get_default_context().database.get("object", attempt_id)
    data = (row or {}).get("context") or {}
    if (
        not row
        or row.get("entity") != HarnessDraftBlobAttempt._entity_name()
        or any(data.get(key) != value for key, value in attempt_identity.items())
    ):
        raise ResourceNotFoundError(message="Document preparation attempt not found")
    content = await storage.read_attachment(storage_key)
    if (
        content is None
        or len(content) != claim.binary_size
        or hashlib.sha256(content).hexdigest() != claim.binary_digest
    ):
        raise ResourceConflictError(message="Prepared document bytes changed.")
    verdict = await get_attachment_scanner().scan(
        content=content, mime_type=mime_type, filename=filename
    )
    if verdict.status not in {"clean", "skipped"}:
        raise ResourceConflictError(
            message="The document could not be safely prepared."
        )

    async with _preparation_admission(snapshot):
        database = current_graph_transaction_database()
        workspace = await database.lock_record("node", snapshot.workspace_id)
        if not workspace or workspace.get("entity") != Workspace._entity_name():
            raise ResourceNotFoundError(message="Workspace not found")
        workspace_data = workspace.get("context") or {}
        used = int(workspace_data.get("storage_bytes_used") or 0)
        quota = int(workspace_data.get("storage_quota_bytes") or 0)
        if settings.STORAGE_QUOTA_ENFORCE and quota > 0 and used + len(content) > quota:
            raise BadRequestError(message="Workspace storage quota exceeded.")
        await require_current_document_draft(snapshot)
        await _lock_current_claim(snapshot, claim)
        locked_attempt = await database.find_one_and_update(
            "object",
            {
                "id": attempt_id,
                "entity": HarnessDraftBlobAttempt._entity_name(),
                **{f"context.{key}": value for key, value in attempt_identity.items()},
            },
            {"$set": {}},
        )
        if locked_attempt is None:
            raise ResourceConflictError(message="Document preparation attempt changed.")
        thread_row = await database.lock_record("node", snapshot.thread_id)
        if (
            thread_row is None
            or thread_row.get("entity") != ChatThread._entity_name()
            or (thread_row.get("context") or {}).get("user_id") != snapshot.user_id
            or (thread_row.get("context") or {}).get("workspace_id")
            != snapshot.workspace_id
        ):
            raise ResourceNotFoundError(message="Thread not found")
        thread = await get_default_context()._deserialize_entity(ChatThread, thread_row)
        attachment = await Attachment.create(
            filename=filename,
            mime_type=mime_type,
            size=len(content),
            storage_key=storage_key,
            source_type="file",
            uploaded_by=snapshot.user_id,
            owner_kind="chat",
            content_hash=claim.binary_digest,
            scan_status=verdict.status,
            scan_engine=verdict.engine,
            scan_message=verdict.message,
            metadata_status="pending",
            created_at=utc_now_iso(),
        )
        await thread.connect(
            attachment,
            edge=HAS_ATTACHMENT,
            attached_at=utc_now_iso(),
            attached_by=snapshot.user_id,
        )
        await insert_revision_event(
            transaction=database,
            workspace_id=snapshot.workspace_id,
            namespace="chat-attachment-create-v1",
            subject_id=attachment.id,
            revision=1,
            event={
                "actor_kind": "agent",
                "actor_id": thread.agent_id or "integral_native",
                "action": "attachment.create",
                "resource_type": "Attachment",
                "resource_id": attachment.id,
                "before": None,
                "after": {
                    "filename": filename,
                    "mime_type": mime_type,
                    "size": len(content),
                    "owner_kind": "chat",
                },
                "scope": f"chat_thread:{snapshot.thread_id}",
            },
        )
        await database.find_one_and_update(
            "node",
            {"id": snapshot.workspace_id},
            {"$inc": {"context.storage_bytes_used": len(content)}},
        )
        await get_default_context()._evict_from_cache(snapshot.workspace_id)
        await database.find_one_and_update(
            "object",
            {"id": attempt_id},
            {
                "$set": {
                    "context.state": "published",
                    "context.attachment_id": attachment.id,
                }
            },
        )
        receipt = await database.find_one_and_update(
            "object",
            {
                "id": claim.record_id,
                "context.state": "pending",
                "_jv_claim": claim.token,
                "_jv_claim_until": {"$gt": time.time()},
            },
            {
                "$set": {
                    "context.state": "prepared",
                    "context.attachment_id": attachment.id,
                },
                "$unset": {"_jv_claim": "", "_jv_claim_until": ""},
            },
        )
        if receipt is None:
            raise ResourceConflictError(
                message="Document preparation expired or changed."
            )
        return attachment.id


async def claim_draft_preparation(
    snapshot: DocumentDraftSnapshot,
    *,
    binary_digest: str,
    binary_size: int,
    lease_seconds: float = 600,
) -> DraftPreparation:
    """Claim one preparation; an expired worker's native claim can be replaced."""
    _require_transactional_store()
    require_outside_graph_transaction()
    if lease_seconds <= 0:
        raise ValueError("A positive preparation lease is required")
    identity = _identity(snapshot, binary_digest, binary_size)
    # This public Core seam must bind bytes produced by the reviewed source,
    # not merely trust a digest supplied by a caller. Rendering and parser
    # checks stay outside the short graph transaction.
    from app.services.document_drafts import render_document_draft

    await require_current_document_draft(snapshot)
    rendered = await asyncio.to_thread(
        render_document_draft,
        title=snapshot.title,
        body=snapshot.body,
        format=snapshot.format,
    )
    if (
        hashlib.sha256(rendered.content).hexdigest() != binary_digest
        or len(rendered.content) != binary_size
    ):
        raise ResourceConflictError(
            message="Rendered document does not match its reviewed source."
        )
    record_id = _record_id(identity)
    prepared = None
    async with _preparation_admission(snapshot):
        await require_current_document_draft(snapshot)
        database = current_graph_transaction_database()
        await HarnessDraftPreparationRecord.create_if_absent(id=record_id, **identity)
        row = await database.find_one_and_update(
            "object",
            {"id": record_id, "entity": HarnessDraftPreparationRecord._entity_name()},
            {"$set": {}},
        )
        current = (row or {}).get("context") or {}
        if any(current.get(key) != value for key, value in identity.items()):
            raise ResourceConflictError(
                message="Document preparation identity changed."
            )
        if current.get("state") == "prepared":
            attachment_id = str(current.get("attachment_id") or "")
            prepared = DraftPreparation(
                record_id,
                snapshot.source_digest,
                binary_digest,
                binary_size,
                attachment_id=attachment_id,
            )
        else:
            if current.get("state") != "pending":
                raise ResourceConflictError(
                    message="Document preparation is unavailable."
                )
            _, token = await claim_record(
                database, "object", record_id, stale_seconds=lease_seconds
            )
            if not token:
                raise ResourceConflictError(
                    message="This document is still being prepared. Try again shortly."
                )
            return DraftPreparation(
                record_id,
                snapshot.source_digest,
                binary_digest,
                binary_size,
                token=token,
            )
    assert prepared is not None
    storage_key = await _verify_prepared_bytes(
        snapshot, prepared.attachment_id, binary_digest, binary_size
    )
    async with _preparation_admission(snapshot):
        current_key = await _validate_prepared_attachment(
            snapshot, prepared.attachment_id, binary_digest, binary_size
        )
        await require_current_document_draft(snapshot)
        row = await current_graph_transaction_database().find_one_and_update(
            "object",
            {
                "id": record_id,
                "context.state": "prepared",
                "context.attachment_id": prepared.attachment_id,
            },
            {"$set": {}},
        )
        if row is None or current_key != storage_key:
            raise ResourceConflictError(message="Prepared document changed. Try again.")
        return prepared


async def _validate_prepared_attachment(
    snapshot: DocumentDraftSnapshot, attachment_id: str, digest: str, size: int
) -> str:
    from app.models.nodes import Attachment
    from app.services.attachment_agent import validate_chat_attachment_source
    from app.services.authority_facts import primary_node_record

    if not attachment_id:
        raise ResourceNotFoundError(message="Prepared document not found")
    transaction = current_graph_transaction_database(required=False)
    row = (
        await transaction.lock_record("node", attachment_id)
        if transaction is not None
        else await primary_node_record(
            get_default_context().database, Attachment, attachment_id
        )
    )
    if row is not None and row.get("entity") != Attachment._entity_name():
        row = None
    attachment, _, error = await validate_chat_attachment_source(
        user_id=snapshot.user_id,
        attachment_id=attachment_id,
        thread_id=snapshot.thread_id,
        workspace_id=snapshot.workspace_id,
    )
    data = (row or {}).get("context") or {}
    if (
        error
        or attachment is None
        or data.get("owner_kind") != "chat"
        or data.get("uploaded_by") != snapshot.user_id
        or data.get("scan_status") not in {"clean", "skipped"}
        or not data.get("storage_key")
        or data.get("content_hash") != digest
        or data.get("size") != size
    ):
        raise ResourceNotFoundError(message="Prepared document not found")
    return str(data["storage_key"])


async def _verify_prepared_bytes(
    snapshot: DocumentDraftSnapshot, attachment_id: str, digest: str, size: int
) -> str:
    """Inspect stored bytes outside the graph transaction and fail closed."""
    from app.services.attachment_storage import get_attachment_storage_service

    await require_current_document_draft(snapshot)
    key = await _validate_prepared_attachment(snapshot, attachment_id, digest, size)
    content = await get_attachment_storage_service().read_attachment(key)
    if (
        content is None
        or len(content) != size
        or hashlib.sha256(content).hexdigest() != digest
    ):
        raise ResourceConflictError(
            message="Prepared document bytes changed. Prepare it again."
        )
    return key


async def finish_draft_preparation(
    snapshot: DocumentDraftSnapshot, claim: DraftPreparation, *, attachment_id: str
) -> None:
    """Atomically bind the exact live chat attachment to a still-current claim."""
    _require_transactional_store()
    require_outside_graph_transaction()
    identity = _identity(snapshot, claim.binary_digest, claim.binary_size)
    if (
        not claim.token
        or claim.source_digest != snapshot.source_digest
        or claim.record_id != _record_id(identity)
    ):
        raise ResourceConflictError(message="Document preparation claim is invalid.")
    storage_key = await _verify_prepared_bytes(
        snapshot, attachment_id, claim.binary_digest, claim.binary_size
    )
    async with _preparation_admission(snapshot):
        current_key = await _validate_prepared_attachment(
            snapshot, attachment_id, claim.binary_digest, claim.binary_size
        )
        await require_current_document_draft(snapshot)
        if current_key != storage_key:
            raise ResourceConflictError(message="Prepared document changed. Try again.")
        database = current_graph_transaction_database()
        row = await database.find_one_and_update(
            "object",
            {
                "id": claim.record_id,
                "entity": HarnessDraftPreparationRecord._entity_name(),
                "context.state": "pending",
                "_jv_claim": claim.token,
                "_jv_claim_until": {"$gt": time.time()},
                **{f"context.{key}": value for key, value in identity.items()},
            },
            {
                "$set": {
                    "context.state": "prepared",
                    "context.attachment_id": attachment_id,
                },
                "$unset": {"_jv_claim": "", "_jv_claim_until": ""},
            },
        )
        if row is None:
            raise ResourceConflictError(
                message="Document preparation expired or changed. Try again."
            )
