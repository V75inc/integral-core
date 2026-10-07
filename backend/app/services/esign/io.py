"""Read generated document binaries for signing."""

from __future__ import annotations

from typing import Optional, Tuple

from app.models.nodes import Attachment, GeneratedDocument
from app.services.attachment_storage import get_attachment_storage_service


async def load_generated_pdf(
    generated_document_id: str,
) -> Tuple[Optional[bytes], Optional[GeneratedDocument]]:
    gd = await GeneratedDocument.get(generated_document_id)
    if not gd:
        return None, None
    att_id = str(getattr(gd, "attachment_id", "") or "")
    if not att_id:
        return None, gd
    att = await Attachment.get(att_id)
    if not att or not att.storage_key:
        return None, gd
    storage = get_attachment_storage_service()
    blob = await storage.read_attachment(att.storage_key)
    return blob, gd
