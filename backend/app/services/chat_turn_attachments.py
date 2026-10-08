"""Scope and revision binding for durable chat file references."""

from __future__ import annotations

import hashlib
from typing import Any

from app.models.edges import HAS_ATTACHMENT
from app.models.nodes import Attachment, ChatThread
from app.schemas.agentive.work import WorkError

_FILE_FIELDS = {"type", "attachment_id", "filename", "mime_type", "size"}
_BINDING_FIELDS = {
    "attachment_id",
    "filename",
    "mime_type",
    "size",
    "storage_key",
    "content_hash",
    "uploaded_by",
    "owner_kind",
}


async def capture_chat_attachment_bindings(
    *,
    thread: ChatThread,
    principal_id: str,
    parts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Bind exact canonical file parts to current uploaded thread artifacts."""
    bindings = []
    graph = await thread.get_context()
    for part in parts:
        if part.get("type") != "file":
            continue
        if set(part) != _FILE_FIELDS:
            raise WorkError("work.policy_denied", "chat file part is invalid")
        attachment = await Attachment.get(str(part.get("attachment_id") or ""))
        if (
            thread.user_id != principal_id
            or attachment is None
            or attachment.source_type != "file"
            or attachment.uploaded_by != principal_id
            or attachment.scan_status not in {"clean", "skipped"}
            or not attachment.storage_key
            or len(attachment.content_hash) != 64
            or any(char not in "0123456789abcdef" for char in attachment.content_hash)
            or not await graph.find_edges_between(
                thread.id, attachment.id, edge_class=HAS_ATTACHMENT
            )
        ):
            raise WorkError("work.policy_denied", "chat attachment is unavailable")
        canonical_part = {
            "type": "file",
            "attachment_id": attachment.id,
            "filename": attachment.filename,
            "mime_type": attachment.mime_type,
            "size": attachment.size,
        }
        if part != canonical_part:
            raise WorkError(
                "work.policy_denied", "chat attachment presentation changed"
            )
        binding = {
            key: getattr(attachment, "id" if key == "attachment_id" else key)
            for key in _BINDING_FIELDS
        }
        binding["extracted_text_digest"] = hashlib.sha256(
            (attachment.extracted_text or "").encode("utf-8")
        ).hexdigest()
        bindings.append(binding)
    return bindings


async def assert_chat_attachment_bindings(
    *,
    thread: ChatThread,
    principal_id: str,
    parts: list[dict[str, Any]],
    expected: list[dict[str, Any]],
) -> None:
    """Recheck accepted file revision and ownership before native preparation."""
    current = await capture_chat_attachment_bindings(
        thread=thread, principal_id=principal_id, parts=parts
    )
    if current != expected:
        raise WorkError("work.policy_denied", "accepted chat attachment changed")


async def assert_chat_attachment_read_inputs(*, item: Any) -> None:
    """Revalidate accepted uploaded files at the model's text-read boundary.

    Called inside the broker's current-lease transaction. Other authorized
    workspace attachments remain ordinary scoped reads; this contract applies
    to files submitted as canonical input to this particular chat turn.
    """
    if item.kind != "chat_turn":
        return
    from app.services.chat_turn_worker_input import load_claimed_chat_turn_input

    # Restoration authenticates all submitted artifacts, including their
    # extracted text digest. A changed input fails before capability dispatch.
    await load_claimed_chat_turn_input(item)
