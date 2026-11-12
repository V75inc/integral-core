"""Release exact chat-owned file bytes for a user's approval review."""

from __future__ import annotations

import hashlib
import re
from contextlib import contextmanager
from contextvars import ContextVar

from jvspatial.core.context import get_default_context

from app.api.errors import ResourceConflictError, ResourceNotFoundError
from app.models.nodes import Attachment, ChatThread
from app.services.app_operations.transaction_scope import (
    current_graph_transaction_database,
    require_outside_graph_transaction,
)
from app.services.attachment_agent import validate_chat_attachment_source
from app.services.attachment_storage import get_attachment_storage_service
from app.services.authority_facts import primary_node_record
from app.services.release_admission import release_admission

_preinspected = ContextVar("chat_attachment_preinspection", default=None)


@contextmanager
def bind_preinspected_attachment(scope: dict, result: tuple):
    """Bind host-inspected bytes for a subsequent fenced proposal only."""
    token = _preinspected.set((scope, result))
    try:
        yield
    finally:
        _preinspected.reset(token)


async def _current_file(
    *, user_id: str, workspace_id: str, thread_id: str, attachment_id: str
) -> dict:
    database = current_graph_transaction_database(required=False)
    if database is not None:
        # Filing takes Attachment then ChatThread; use the same lock order.
        attachment_row = await database.lock_record("node", attachment_id)
        thread_row = await database.lock_record("node", thread_id)
    else:
        database = get_default_context().database
        attachment_row = await primary_node_record(database, Attachment, attachment_id)
        thread_row = await primary_node_record(database, ChatThread, thread_id)
    attachment_data = (attachment_row or {}).get("context") or {}
    thread_data = (thread_row or {}).get("context") or {}
    attachment, _, error = await validate_chat_attachment_source(
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
        attachment_id=attachment_id,
    )
    digest = attachment_data.get("content_hash")
    if (
        error
        or attachment is None
        or (attachment_row or {}).get("entity") != Attachment._entity_name()
        or (thread_row or {}).get("entity") != ChatThread._entity_name()
        or attachment_data.get("owner_kind") != "chat"
        or attachment_data.get("uploaded_by") != user_id
        or attachment_data.get("source_type") != "file"
        or attachment_data.get("scan_status") not in {"clean", "skipped"}
        or not attachment_data.get("storage_key")
        or not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or type(attachment_data.get("size")) is not int
        or thread_data.get("user_id") != user_id
        or thread_data.get("workspace_id") != workspace_id
        or thread_data.get("archived") is not False
    ):
        raise ResourceNotFoundError(message="Chat attachment not found")
    return {
        key: attachment_data.get(key)
        for key in (
            "storage_key",
            "content_hash",
            "size",
            "mime_type",
            "owner_kind",
            "uploaded_by",
            "scan_status",
            "source_type",
        )
    }


async def read_chat_attachment_for_review(
    *, user_id: str, workspace_id: str, thread_id: str, attachment_id: str
) -> tuple[bytes, str]:
    """Read bytes outside locks, then re-admit the same source before release."""
    blob, mime_type, _ = await inspect_chat_attachment_for_review(
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
        attachment_id=attachment_id,
    )
    return blob, mime_type


async def inspect_chat_attachment_for_review(
    *, user_id: str, workspace_id: str, thread_id: str, attachment_id: str
) -> tuple[bytes, str, dict]:
    """Inspect bytes and retain private source facts for the application fence."""
    scope = {
        "user_id": user_id,
        "workspace_id": workspace_id,
        "thread_id": thread_id,
        "attachment_id": attachment_id,
    }
    bound = _preinspected.get()
    if bound is not None:
        expected_scope, result = bound
        if expected_scope != scope or await _current_file(**scope) != result[2]:
            raise ResourceConflictError(
                message="The file changed and cannot be reviewed."
            )
        return result
    require_outside_graph_transaction()
    async with release_admission(user_id, workspace_id):
        source = await _current_file(**scope)
    blob = await get_attachment_storage_service().read_attachment(source["storage_key"])
    if (
        blob is None
        or len(blob) != source["size"]
        or hashlib.sha256(blob).hexdigest() != source["content_hash"]
    ):
        raise ResourceConflictError(message="The file changed and cannot be reviewed.")
    async with release_admission(user_id, workspace_id):
        if await _current_file(**scope) != source:
            raise ResourceConflictError(
                message="The file changed and cannot be reviewed."
            )
        return blob, str(source["mime_type"] or "application/octet-stream"), source


async def file_reviewed_chat_attachment(
    *,
    user_id: str,
    workspace_id: str,
    thread_id: str,
    attachment_id: str,
    entry_id: str,
    expected_sha256: str,
    expected_size: int,
) -> dict:
    """Recheck approved bytes before entering the existing Entry effect boundary."""
    from app.models.nodes import Entry
    from app.services.attachment_agent import attach_uploaded_file_to_entry
    from app.services.entry_write_scope import (
        entry_write_scope,
        is_postgres_entry_transaction,
    )

    if (
        not isinstance(expected_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
        or type(expected_size) is not int
        or expected_size < 0
    ):
        raise ResourceConflictError(
            message="Review this file again before attaching it."
        )
    blob, _, source = await inspect_chat_attachment_for_review(
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
        attachment_id=attachment_id,
    )
    if (
        len(blob) != expected_size
        or hashlib.sha256(blob).hexdigest() != expected_sha256
    ):
        raise ResourceConflictError(
            message="The approved file changed. Review it again."
        )

    async with (
        entry_write_scope(entry_id) as graph,
        release_admission(user_id, workspace_id),
    ):
        database = current_graph_transaction_database(required=False)
        if database is None or not is_postgres_entry_transaction(graph.database):
            raise ResourceConflictError(
                message="Safe file approval is unavailable on this storage adapter."
            )
        entry = await database.lock_record("node", entry_id)
        attachment = await database.lock_record("node", attachment_id)
        thread = await database.lock_record("node", thread_id)
        data = (attachment or {}).get("context") or {}
        thread_data = (thread or {}).get("context") or {}
        if (
            not entry
            or entry.get("entity") != Entry._entity_name()
            or not attachment
            or attachment.get("entity") != Attachment._entity_name()
            or not thread
            or thread.get("entity") != ChatThread._entity_name()
            or thread_data.get("user_id") != user_id
            or thread_data.get("workspace_id") != workspace_id
            or thread_data.get("archived") is not False
            or data.get("owner_kind") != "chat"
            or any(data.get(key) != value for key, value in source.items())
        ):
            raise ResourceConflictError(
                message="The approved file changed. Review it again."
            )
        return await attach_uploaded_file_to_entry(
            user_id=user_id, attachment_id=attachment_id, entry_id=entry_id
        )
