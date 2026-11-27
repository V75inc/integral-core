"""WhatsApp visitor exchanges — tool-free public profile history (I-GRAPH-02)."""

from __future__ import annotations

from typing import ClassVar, Optional

from jvspatial.core import Object

from app.models.indexing import attribute


class WhatsAppVisitorExchange(Object):
    """One completed public-profile exchange. The session key is a hash."""

    __entity_name__: ClassVar[Optional[str]] = "WhatsAppVisitorExchange"

    session_key: str = attribute(default="")
    event_id: str = attribute(default="")
    visitor_text: str = ""
    assistant_text: str = ""
    created_at: Optional[str] = None
