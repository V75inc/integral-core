"""Scope and revision binding for durable chat file references."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from app.models.edges import HAS_ATTACHMENT
from app.models.nodes import Attachment, ChatThread
from app.schemas.agentive.work import WorkError
from app.services.attachment_metadata import get_metadata_extraction_service

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
_PENDING_METADATA = {"pending", "processing"}


async def prepare_chat_attachment_bindings(
    *,
    thread: ChatThread,
    principal_id: str,
    parts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Finish extracting new uploads before signing the turn's text digest."""
    pending: set[str] = set()
    bindings = await capture_chat_attachment_bindings(
        thread=thread, principal_id=principal_id, parts=parts, pending=pending
    )
    if not pending:
        return bindings

    service = get_metadata_extraction_service()
    try:
        async with asyncio.timeout(30):
            for attachment_id in pending:
                # The shared extraction continues even if this request times out.
                service.schedule_extraction(attachment_id)
            await asyncio.gather(
                *(service.extract_now(attachment_id) for attachment_id in pending)
            )
    except TimeoutError as exc:
        raise WorkError(
            "work.attachment_processing",
            "Your file is still being prepared. Try sending your message again shortly.",
        ) from exc
    except Exception as exc:
        raise WorkError(
            "work.attachment_processing",
            "Your file could not be prepared. Try attaching it again.",
        ) from exc
    return await capture_chat_attachment_bindings(
        thread=thread, principal_id=principal_id, parts=parts
    )


async def capture_chat_attachment_bindings(
    *,
    thread: ChatThread,
    principal_id: str,
    parts: list[dict[str, Any]],
    pending: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Bind exact canonical file parts to current uploaded thread artifacts."""
    bindings = []
    graph = await thread.get_context()
    for part in parts:
        if part.get("type") != "file":
            continue
        if set(part) != _FILE_FIELDS:
            raise WorkError("work.policy_denied", "chat file part is invalid")
        # Extraction writes derived fields directly. A cached node can still
        # contain the empty text that was present when the upload completed.
        attachment = await Attachment.find_one(
            {"id": str(part.get("attachment_id") or "")}
        )
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
        if pending is not None and attachment.metadata_status in _PENDING_METADATA:
            pending.add(attachment.id)
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
    # The provider already admitted the host event before starting. Its own
    # approved effects may consume those source approvals. This read boundary
    # rechecks file revision and current scope, not the pre-effect host state.
    await load_claimed_chat_turn_input(item, recheck_host_control=False)


async def retained_chat_attachment_references(
    *,
    thread: ChatThread,
    principal_id: str,
    workspace_id: str,
    exclude_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Bounded discovery of this conversation's still-chat-owned uploads.

    These are historical locators, not current-turn input bindings or read/write
    authority. Reads and filing reauthorize the source at their own boundaries.
    Never widen workspace attachment listing to include private conversations.
    """
    from app.services.attachment_urls import is_visible_to_user
    from app.services.workspace_permissions import can_access_workspace

    current = await ChatThread.find_one({"id": thread.id})
    if (
        current is None
        or current.archived
        or current.user_id != principal_id
        or current.workspace_id != workspace_id
        or await can_access_workspace(principal_id, workspace_id) == "none"
    ):
        return {"files": [], "truncated": False}
    thread = current
    limit = 20
    candidates = await thread.nodes(
        edge=[HAS_ATTACHMENT], node=["Attachment"], direction="out", limit=limit + 1
    )
    files = []
    for candidate in candidates[:limit]:
        if candidate.id in (exclude_ids or set()):
            continue
        # Refresh mutable scan/ownership state; a historical message part alone
        # is not evidence that a file remains available in this conversation.
        attachment = await Attachment.find_one({"id": candidate.id})
        if (
            attachment is None
            or attachment.owner_kind != "chat"
            or attachment.uploaded_by != principal_id
            or attachment.source_type != "file"
            or attachment.scan_status not in {"clean", "skipped"}
            or not attachment.storage_key
            or not is_visible_to_user(attachment)
        ):
            continue
        owners = await attachment.nodes(
            edge=[HAS_ATTACHMENT], node=["ChatThread"], direction="in", limit=2
        )
        if len(owners) != 1 or owners[0].id != thread.id:
            continue
        files.append(
            {
                "attachment_id": attachment.id,
                "filename": attachment.filename[:512],
                "mime_type": attachment.mime_type[:128],
                "size": attachment.size,
            }
        )
    return {"files": files, "truncated": len(candidates) > limit}


async def recent_chat_upload_context(
    *,
    thread: ChatThread,
    principal_id: str,
    workspace_id: str,
    through_message_id: str | None = None,
) -> dict[str, Any] | None:
    """Keep a recent user upload associated with its request across clarification.

    This is historical read context, never a selection or filing permit. The
    harness's public message reader bounds discovery and verifies CONTAINS;
    attachment reads still independently enforce current scope and ownership.
    Pydantic AI's model history does not retain host-only upload context, so
    this substrate association must be recovered from canonical chat parts.
    """
    from app.agentive.harness.adaptive import recent_public_messages
    from app.services.workspace_permissions import can_access_workspace

    current = await ChatThread.find_one({"id": thread.id})
    if (
        current is None
        or current.archived
        or current.user_id != principal_id
        or current.workspace_id != workspace_id
        or await can_access_workspace(principal_id, workspace_id) == "none"
    ):
        return None
    messages = await recent_public_messages(current)
    if through_message_id:
        accepted = next(
            (
                i
                for i, message in enumerate(messages)
                if message.id == through_message_id
            ),
            None,
        )
        if accepted is None:
            return None
        messages = messages[: accepted + 1]
    for message in reversed(messages):
        if message.role != "user":
            continue
        parts = message.parts or []
        files = [p for p in parts if p.get("type") == "file"]
        if not files:
            continue
        # Do not fall back to an older upload if the newest is no longer
        # readable/chat-owned: that would silently substitute the source.
        try:
            content = await current_chat_attachment_content(
                thread=current,
                principal_id=principal_id,
                workspace_id=workspace_id,
                parts=files,
            )
        except WorkError:
            return None
        return {
            "message_id": message.id,
            "user_request": "\n".join(
                str(p.get("text") or "") for p in parts if p.get("type") == "text"
            )[:4000],
            "historical_upload": True,
            **content,
        }
    return None


async def current_chat_attachment_content(
    *,
    thread: ChatThread,
    principal_id: str,
    workspace_id: str,
    parts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Read bounded text for verified canonical uploads, not arbitrary locators.

    The caller supplies accepted canonical message parts, not model arguments.
    Source association is not filing authority; the normal target policy and
    reviewed create+attach batch still govern every write.
    """
    from app.services.attachment_agent import get_attachment_text
    from app.services.workspace_permissions import can_access_workspace

    file_parts = [p for p in parts if p.get("type") == "file"]
    if not file_parts:
        return {"files": [], "truncated": False}
    current = await ChatThread.find_one({"id": thread.id})
    if (
        current is None
        or current.archived
        or current.user_id != principal_id
        or current.workspace_id != workspace_id
        or await can_access_workspace(principal_id, workspace_id) == "none"
    ):
        raise WorkError("work.policy_denied", "chat attachment is unavailable")
    # Revalidate canonical metadata, immutable content hash, scan and ownership;
    # never trust a filename/ID pasted into conversational text as an upload.
    await capture_chat_attachment_bindings(
        thread=current,
        principal_id=principal_id,
        parts=file_parts,
    )
    files = []
    remaining = 24000
    text_parts = [p for p in file_parts if not p["mime_type"].startswith("image/")]
    for part in text_parts[:4]:
        if remaining <= 0:
            break
        attachment = await Attachment.find_one({"id": part["attachment_id"]})
        if attachment is None:
            raise WorkError("work.policy_denied", "chat attachment is unavailable")
        owners = await attachment.nodes(
            edge=[HAS_ATTACHMENT], node=["ChatThread"], direction="in", limit=2
        )
        if (
            attachment.owner_kind != "chat"
            or len(owners) != 1
            or owners[0].id != current.id
        ):
            raise WorkError("work.policy_denied", "chat attachment is unavailable")
        read = await get_attachment_text(
            principal_id, attachment.id, max_chars=min(12000, remaining)
        )
        body = str(read.get("text") or "")[: min(12000, remaining)]
        remaining -= len(body)
        files.append(
            {
                "attachment_id": attachment.id,
                "filename": attachment.filename[:512],
                "mime_type": attachment.mime_type[:128],
                "text": body,
                "metadata_status": read.get("metadata_status"),
                "truncated": bool(read.get("truncated")),
                "content_untrusted": True,
            }
        )
    return {"files": files, "truncated": len(files) < len(text_parts)}
