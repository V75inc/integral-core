"""Per-user saved signature vault (PNG attachments on User)."""

from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional

from app.models.edges import HAS_ATTACHMENT
from app.models.nodes import Attachment, User
from app.services.attachment_storage import get_attachment_storage_service
from app.services.change_event import emit_change_event
from app.services.entry_write_scope import is_postgres_entry_transaction
from app.services.signature_cleanup import (
    retain_signature_cleanup,
    signature_write_scope,
)
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

SIGNATURE_ROLE = "signature"


async def _find_user(user_id: str) -> Optional[User]:
    from app.services.permissions import get_user_node

    # Auth IDs and profile IDs share one current-identity resolver. A cached
    # profile is not evidence that its linked sign-in remains active.
    return await get_user_node(user_id)


async def _find_signature_attachment(user: User) -> Optional[Attachment]:
    from app.api.errors import ResourceConflictError

    ctx = await user.get_context()
    # Single-hop, bounded signature-edge selection. Refuse ambiguous legacy
    # state instead of deleting whichever attachment happened to hydrate first.
    rows = await ctx.database.find(
        "edge",
        {
            "entity": HAS_ATTACHMENT._entity_name(),
            "source": user.id,
            "context.role": SIGNATURE_ROLE,
        },
        limit=2,
    )
    if not rows:
        return None
    if len(rows) != 1 or not isinstance(rows[0].get("target"), str):
        raise ResourceConflictError(
            message="Saved signature needs review before replacement"
        )
    attachment = await Attachment.find_one({"id": rows[0]["target"]})
    if attachment is None:
        raise ResourceConflictError(message="Saved signature attachment is unavailable")
    return attachment


async def list_user_signatures(user_id: str) -> List[Dict[str, Any]]:
    """List the current signature in the caller’s vault."""
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
    """Read the current signature bytes without changing saved state."""
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
    """Store a replacement before retiring the previous signature."""
    if not png_bytes:
        raise ValueError("Signature image is required")
    initial_user = await _find_user(user_id)
    if initial_user is None:
        raise ValueError("User not found")
    canonical_user_id = initial_user.id
    attachment = None
    storage_key = ""
    transactional = False
    replacement_ready = False
    try:
        async with signature_write_scope(canonical_user_id) as graph:
            transactional = is_postgres_entry_transaction(graph.database)
            user = await User.find_one({"id": canonical_user_id})
            if user is None:
                raise ValueError("User not found")
            existing = await _find_signature_attachment(user)
            storage = get_attachment_storage_service()
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
            # Root the provisional node in the same unit of work. On PostgreSQL
            # neither it nor old-signature retirement is visible before commit.
            await user.connect(
                attachment,
                edge=HAS_ATTACHMENT,
                attached_at=now,
                attached_by=user_id,
                role=SIGNATURE_ROLE,
            )
            storage_key = storage.build_storage_key(
                user.id, attachment.id, attachment.filename
            )
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
            if str(result.get("path") or "") != storage_key:
                raise OSError("Signature storage did not confirm the expected file")
            attachment.storage_key = storage_key
            await attachment.save()
            replacement_ready = True
            if existing:
                await retain_signature_cleanup(
                    user_id=user.id,
                    attachment_id=existing.id,
                    filename=existing.filename,
                    storage_key=existing.storage_key,
                )
                await existing.delete()
            await emit_change_event(
                actor_kind="human",
                actor_id=user_id,
                action="user.update",
                resource_type="User",
                resource_id=user.id,
                before={"attachment_id": existing.id} if existing else None,
                after={"attachment_id": attachment.id, "label": label},
                scope=f"user:{user.id}",
            )
    except BaseException:
        # PG rolls the complete graph swap back. Local development stores have
        # no rollback; remove only an unready replacement, never the old file.
        if attachment is not None:
            try:
                if not transactional and not replacement_ready:
                    await attachment.delete()
                if storage_key:
                    async with signature_write_scope(canonical_user_id):
                        await retain_signature_cleanup(
                            user_id=canonical_user_id,
                            attachment_id=attachment.id,
                            filename=attachment.filename,
                            storage_key=storage_key,
                        )
            except Exception:
                logger.exception(
                    "Incomplete signature replacement retained for recovery"
                )
        raise
    return {
        "id": attachment.id,
        "filename": attachment.filename,
        "mime_type": attachment.mime_type,
        "size": attachment.size,
        "created_at": attachment.created_at,
        "label": label,
    }


def decode_signature_upload(raw: str) -> bytes:
    """Decode an uploaded signature payload without changing saved state."""
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
    """Retire an owned signature and queue its file cleanup after commit."""
    initial_user = await _find_user(user_id)
    if initial_user is None:
        return False
    async with signature_write_scope(initial_user.id):
        user = await User.find_one({"id": initial_user.id})
        att = await Attachment.find_one({"id": attachment_id})
        if user is None or att is None:
            return False
        ctx = await user.get_context()
        edges = await ctx.find_edges_between(user.id, att.id, edge_class=HAS_ATTACHMENT)
        if not any(getattr(e, "role", None) == SIGNATURE_ROLE for e in edges):
            raise PermissionError("Not your signature")
        await retain_signature_cleanup(
            user_id=user.id,
            attachment_id=att.id,
            filename=att.filename,
            storage_key=att.storage_key,
        )
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
