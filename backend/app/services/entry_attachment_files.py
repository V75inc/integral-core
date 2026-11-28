"""Stage Entry file bytes outside graph locks and link only after fresh admission."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from anyio import CancelScope
from jvspatial.core.context import get_default_context

from app.models.edges import HAS_ATTACHMENT
from app.models.nodes import Attachment, Entry
from app.services.app_operations.transaction_scope import (
    require_outside_graph_transaction,
)
from app.services.attachment_entry_write import attachment_entry_write
from app.services.attachment_storage import AttachmentStorageService
from app.services.entry_write_scope import is_postgres_entry_transaction
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


class AttachmentStorageWriteError(RuntimeError):
    """Storage did not acknowledge the new file."""


@asynccontextmanager
async def prepared_entry_file(
    entry: Entry,
    user_id: str,
    filename: str,
    mime_type: str,
    content: bytes,
    *,
    storage: AttachmentStorageService,
    content_hash: str = "",
    metadata: dict | None = None,
) -> AsyncIterator[Attachment]:
    """Reserve a unique key without persisting an unattached graph node.

    Keep this context open until the final graph transaction has committed.
    A rejected upload owns only its own keys, even when its bytes are a duplicate.
    """
    require_outside_graph_transaction()
    attachment = Attachment(
        filename=filename,
        mime_type=mime_type,
        size=len(content),
        uploaded_by=user_id,
        content_hash=content_hash,
        created_at=utc_now_iso(),
    )
    expected = AttachmentStorageService.build_storage_key(
        entry.id, attachment.id, filename
    )
    try:
        try:
            stored = await storage.save_attachment(
                entry_id=entry.id,
                attachment_id=attachment.id,
                filename=filename,
                content=content,
                mime_type=mime_type,
                metadata=(
                    {**metadata, "attachment_id": attachment.id} if metadata else None
                ),
            )
            attachment.storage_key = str(stored.get("path") or expected)
        except Exception as exc:
            raise AttachmentStorageWriteError(
                "Attachment storage write failed"
            ) from exc
        yield attachment
    except BaseException:
        with CancelScope(shield=True):
            for key in {
                attachment.thumb_storage_key,
                attachment.storage_key or expected,
            } - {""}:
                try:
                    await storage.delete_attachment(key)
                except Exception:
                    logger.exception(
                        "Could not reclaim uncommitted attachment %s", attachment.id
                    )
        raise


async def link_prepared_file(
    entry: Entry, attachment: Attachment, *, charge_storage: bool = False
) -> None:
    """Wire a prepared file and its denormalized index in the admitted graph scope."""
    from app.services.workspace_storage_usage import (
        charge_entry_upload,
        decrement_for_entry,
    )

    if charge_storage:
        await charge_entry_upload(entry, attachment.size)
    try:
        await attachment.save()
        await entry.connect(
            attachment,
            edge=HAS_ATTACHMENT,
            attached_at=utc_now_iso(),
            attached_by=attachment.uploaded_by,
        )
        entry.attachment_ids = [*(entry.attachment_ids or []), attachment.id]
        await entry.save()
    except BaseException:
        if not is_postgres_entry_transaction(get_default_context().database):
            try:
                await attachment.delete()
            finally:
                if charge_storage:
                    await decrement_for_entry(entry, attachment.size)
        raise


async def attach_entry_file(
    entry: Entry, user_id: str, filename: str, mime_type: str, content: bytes
) -> Attachment:
    """Persist a reviewed or tool-produced file with the caller's write authority."""
    from app.services.attachment_storage import get_attachment_storage_service

    async with attachment_entry_write(entry, user_id):
        pass
    async with prepared_entry_file(
        entry,
        user_id,
        filename,
        mime_type,
        content,
        storage=get_attachment_storage_service(),
    ) as attachment:
        async with attachment_entry_write(entry, user_id) as current:
            await link_prepared_file(current, attachment)
    return attachment
