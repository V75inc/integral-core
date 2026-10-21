"""Native visual input from canonical chat uploads, with live dispatch checks."""

from __future__ import annotations

import base64
import hashlib
import io
from typing import Any

from PIL import Image, UnidentifiedImageError

from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.model_errors import NativeModelRequestError
from app.agentive.harness.pydantic_ai_compat import BinaryContent
from app.models.edges import CONTAINS, HAS_ATTACHMENT
from app.models.nodes import Attachment, ChatMessage, ChatThread, Entry
from app.schemas.agentive.work import WorkError
from app.schemas.api.ai_chat import ALLOWED_IMAGE_TYPES, MAX_IMAGES_PER_MESSAGE

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000


async def _fresh_node(model: Any, node_id: str) -> Any:
    from jvspatial.core.context import get_default_context

    from app.services.authority_facts import primary_node_record

    graph = get_default_context()
    row = await primary_node_record(graph.database, model, node_id)
    return await graph._deserialize_entity(model, row) if row is not None else None


async def _has_edge(graph: Any, source: str, target: str, edge: Any) -> bool:
    rows = await graph.database.find(
        "edge",
        {"source": source, "target": target, "entity": edge._entity_name()},
        limit=2,
    )
    return (
        len(rows) == 1
        and rows[0].get("source") == source
        and rows[0].get("target") == target
    )


async def _thread(scope: HarnessExecutionScope) -> ChatThread:
    from app.services.workspace_permissions import can_access_workspace

    thread = await _fresh_node(ChatThread, scope.thread_id)
    if (
        thread is None
        or thread.user_id != scope.principal_id
        or thread.workspace_id != scope.workspace_id
        or await can_access_workspace(scope.principal_id, scope.workspace_id) == "none"
    ):
        raise WorkError("work.policy_denied", "chat image is unavailable")
    return thread


def _validate_pixels(data: bytes, mime_type: str) -> None:
    try:
        with Image.open(io.BytesIO(data)) as image:
            if (
                Image.MIME.get(image.format or "") != mime_type
                or image.width * image.height > MAX_IMAGE_PIXELS
            ):
                raise ValueError("unsupported image dimensions or format")
            image.verify()
    except (
        ValueError,
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
    ) as exc:
        raise NativeModelRequestError("image_input_invalid") from exc


async def _read_image(
    thread: ChatThread, attachment_id: str
) -> tuple[Attachment, bytes]:
    from app.services.attachment_agent import _require_entry_read
    from app.services.attachment_storage import get_attachment_storage_service
    from app.services.resource_location import locate_resource

    graph = await thread.get_context()
    if not await _has_edge(graph, thread.id, attachment_id, HAS_ATTACHMENT):
        raise WorkError("work.policy_denied", "chat image is unavailable")
    attachment = await _fresh_node(Attachment, attachment_id)
    if (
        attachment is None
        or attachment.uploaded_by != thread.user_id
        or attachment.source_type != "file"
        or attachment.scan_status not in {"clean", "skipped"}
    ):
        raise WorkError("work.policy_denied", "chat image is unavailable")
    owners = await graph.database.find(
        "edge",
        {"target": attachment_id, "entity": HAS_ATTACHMENT._entity_name()},
        limit=4,
    )
    if len(owners) > 2:
        raise WorkError("work.policy_denied", "chat image ownership is ambiguous")
    for owner in owners:
        if owner.get("source") == thread.id:
            continue
        entry = await _fresh_node(Entry, str(owner.get("source") or ""))
        if entry is None:
            raise WorkError("work.policy_denied", "chat image is unavailable")
        location = await locate_resource("entry", entry.id)
        if location is None or location.workspace_id != thread.workspace_id:
            raise WorkError("work.policy_denied", "chat image is unavailable")
        try:
            await _require_entry_read(thread.user_id, entry)
        except PermissionError as exc:
            raise WorkError("work.policy_denied", "chat image is unavailable") from exc
    if attachment.mime_type not in ALLOWED_IMAGE_TYPES:
        raise NativeModelRequestError("image_input_unsupported")
    if not 0 < attachment.size <= MAX_IMAGE_BYTES:
        raise NativeModelRequestError("image_input_invalid")
    data = await get_attachment_storage_service().read_attachment(
        attachment.storage_key
    )
    if (
        data is None
        or len(data) != attachment.size
        or hashlib.sha256(data).hexdigest() != attachment.content_hash
    ):
        raise WorkError("work.policy_denied", "chat image content changed")
    _validate_pixels(data, attachment.mime_type)
    return attachment, data


async def chat_image_prompt(
    scope: HarnessExecutionScope, message_id: str | None, text: str
) -> str | list[Any] | None:
    """Keep authored text exact and add pixels only from this accepted message."""
    if not message_id:
        return text or None
    message = await _fresh_node(ChatMessage, message_id)
    if (
        message is None
        or message.thread_id != scope.thread_id
        or message.role not in {"user", "system"}
    ):
        raise WorkError("work.policy_denied", "chat image message is unavailable")
    files = [
        p
        for p in message.parts
        if p.get("type") == "file"
        and str(p.get("mime_type") or "").startswith("image/")
    ]
    if not files:
        return text or None
    # Approval outcomes and other server continuations are durable system
    # messages. They carry text, never newly uploaded user images. Historical
    # images remain subject to the per-dispatch validator below.
    if message.role != "user":
        raise WorkError("work.policy_denied", "chat image message is unavailable")
    if len(files) > MAX_IMAGES_PER_MESSAGE:
        raise NativeModelRequestError("image_input_limit")
    thread = await _thread(scope)
    graph = await thread.get_context()
    if not await _has_edge(graph, thread.id, message.id, CONTAINS):
        raise WorkError("work.policy_denied", "chat image message is detached")
    content: list[Any] = [text] if text else []
    for part in files:
        attachment, data = await _read_image(
            thread, str(part.get("attachment_id") or "")
        )
        if part.get("mime_type") != attachment.mime_type:
            raise WorkError("work.policy_denied", "chat image presentation changed")
        content.extend(
            [
                f"Uploaded image: {attachment.filename}",
                BinaryContent(data=data, media_type=attachment.mime_type),
            ]
        )
    return content


async def assert_model_image_access(
    scope: HarnessExecutionScope, payload: dict[str, Any]
) -> None:
    """Revalidate every image, including restored history, before each SDK attempt.

    No remote image URLs or arbitrary browser base64 are admitted. A model's
    data URI must exactly match a current readable upload linked to this chat.
    The source bytes and scan state are checked again on retries; this is not
    an authorization or content cache.
    """
    images: dict[str, str] = {}
    for message in payload.get("messages") or []:
        parts = message.get("content")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict) or part.get("type") != "image_url":
                continue
            image = part.get("image_url")
            url = image.get("url") if isinstance(image, dict) else image
            if not isinstance(url, str) or len(url) > 7_000_100:
                raise NativeModelRequestError("image_input_invalid")
            header, sep, encoded = url.partition(",")
            mime = header.removeprefix("data:").removesuffix(";base64")
            if (
                not sep
                or header != f"data:{mime};base64"
                or mime not in ALLOWED_IMAGE_TYPES
            ):
                raise NativeModelRequestError("image_input_unsupported")
            try:
                data = base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise NativeModelRequestError("image_input_invalid") from exc
            if not 0 < len(data) <= MAX_IMAGE_BYTES:
                raise NativeModelRequestError("image_input_invalid")
            digest = hashlib.sha256(data).hexdigest()
            if digest in images and images[digest] != mime:
                raise NativeModelRequestError("image_input_invalid")
            images[digest] = mime
    if not images:
        return
    thread = await _thread(scope)
    # Lookup is bounded by the actual request's image digests. Matching bytes
    # in another chat or workspace never suffice: each candidate still needs
    # this chat's canonical edge and current owning-record access.
    for digest, mime in images.items():
        candidates = await Attachment.find(
            {"context.content_hash": digest, "context.uploaded_by": scope.principal_id}
        )
        allowed = False
        for candidate in candidates:
            try:
                attachment, _data = await _read_image(thread, candidate.id)
            except WorkError:
                continue
            if attachment.content_hash == digest and attachment.mime_type == mime:
                allowed = True
                break
        if not allowed:
            raise WorkError("work.policy_denied", "chat image is no longer available")
