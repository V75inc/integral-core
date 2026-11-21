"""WhatsAppInboundReceipt — inbound Meta message ids (I-GRAPH-02)."""

from __future__ import annotations

from typing import ClassVar, Optional

from jvspatial.core import Object

from app.models.indexing import attribute


class WhatsAppInboundReceipt(Object):
    """One inbound WhatsApp message id for a channel binding."""

    __entity_name__: ClassVar[Optional[str]] = "WhatsAppInboundReceipt"

    binding_id: str = attribute(default="", indexed=True)
    meta_message_id: str = attribute(default="", indexed=True)
    kind: str = ""
    status: str = ""
    last_error: str = ""
    created_at: Optional[str] = None
