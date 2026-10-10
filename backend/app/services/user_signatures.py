"""Per-user saved signature vault (PNG attachments on User)."""

from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional

from app.models.edges import HAS_ATTACHMENT
from app.models.nodes import Attachment, User
from app.services.attachment_storage import get_attachment_storage_service
from app.services.change_event import emit_change_event
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

SIGNATURE_ROLE = "signature"


async def _find_user(user_id: str) -> Optional[User]:
    user = await User.get(user_id)
    if user:
        return user
    from app.services.permissions import get_user_node

    return await get_user_node(user_id)


async def _find_signature_attachment(user: User) -> Optional[Attachment]:
    atts = await user.nodes(edge=[HAS_ATTACHMENT], direction="out", node=["Attachment"])
    if not atts:
        return None
    ctx = await user.get_context()
    for att in atts:
        edges = await ctx.find_edges_between(user.id, att.id, edge_class=HAS_ATTACHMENT)
        for edge in edges:
            if getattr(edge, "role", None) == SIGNATURE_ROLE:
                return att  # type: ignore[return-value]
    return None


async def list_user_signatures(user_id: str) -> List[Dict[str, Any]]:
    user = await _find_user(user_id)
    if not user:
        return []
    att = await _find_signature_attachment(user)
    if not att:
        return []
    return [
        {
            "id": att.id,
            "filename": att.filename,
            "mime_type": att.mime_type,
            "size": att.size,
            "created_at": att.created_at,
            "label": "Default",
        }
    ]


async def read_user_signature_png(user_id: str) -> Optional[bytes]:
    user = await _find_user(user_id)
    if not user:
        return None
    att = await _find_signature_attachment(user)
    if not att or not att.storage_key:
        return None
    storage = get_attachment_storage_service()
    try:
        return await storage.read_attachment(att.storage_key)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to read user signature for %s", user_id)
        return None


async def save_user_signature(
    user_id: str,
    *,
    png_bytes: bytes,
    label: str = "Default",
) -> Dict[str, Any]:
    if not png_bytes:
        raise ValueError("Signature image is required")
    user = await _find_user(user_id)
    if not user:
        raise ValueError("User not found")

    existing = await _find_signature_attachment(user)
    if existing:
        await delete_user_signature(user_id, existing.id)

    now = utc_now_iso()
    attachment = await Attachment.create(
        filename=f"signature-{user.id}.png",
        mime_type="image/png",
        size=len(png_bytes),
        storage_key="",
        source_type="file",
        external_url="",
        uploaded_by=user_id,
        content_hash="",
        scan_status="skipped",
        metadata_status="complete",
        metadata={"role": SIGNATURE_ROLE, "label": label},
        created_at=now,
    )
    storage = get_attachment_storage_service()
    try:
        result = await storage.save_attachment(
            entry_id=user.id,
            attachment_id=attachment.id,
            filename=attachment.filename,
            content=png_bytes,
            metadata={
                "owner_user_id": user.id,
                "attachment_id": attachment.id,
                "uploaded_by": user_id,
                "role": SIGNATURE_ROLE,
            },
        )
    except Exception:
        await attachment.delete()
        raise
    attachment.storage_key = str(result.get("path") or "")
    await attachment.save()
    await user.connect(
        attachment,
        edge=HAS_ATTACHMENT,
        attached_at=now,
        attached_by=user_id,
        role=SIGNATURE_ROLE,
    )
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="user.update",
        resource_type="User",
        resource_id=user.id,
        before=None,
        after={"attachment_id": attachment.id, "label": label},
        scope=f"user:{user.id}",
    )
    return {
        "id": attachment.id,
        "filename": attachment.filename,
        "mime_type": attachment.mime_type,
        "size": attachment.size,
        "created_at": attachment.created_at,
        "label": label,
    }


def decode_signature_upload(raw: str) -> bytes:
    data = (raw or "").strip()
    if data.startswith("data:"):
        _header, _comma, payload = data.partition(",")
        data = payload
    try:
        png = base64.b64decode(data, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Invalid signature image encoding") from exc
    if not png:
        raise ValueError("Signature image is empty")
    return png


async def delete_user_signature(user_id: str, attachment_id: str) -> bool:
    user = await _find_user(user_id)
    if not user:
        return False
    att = await Attachment.get(attachment_id)
    if not att:
        return False
    ctx = await user.get_context()
    edges = await ctx.find_edges_between(user.id, att.id, edge_class=HAS_ATTACHMENT)
    if not any(getattr(e, "role", None) == SIGNATURE_ROLE for e in edges):
        raise PermissionError("Not your signature")
    if att.storage_key:
        storage = get_attachment_storage_service()
        try:
            await storage.delete_attachment(att.storage_key)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to delete signature blob %s", att.storage_key)
    await att.delete()
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="user.update",
        resource_type="User",
        resource_id=user.id,
        before={"attachment_id": attachment_id},
        after=None,
        scope=f"user:{user.id}",
    )
    return True
