"""Append-only transactional mail attempt log (I-GRAPH-02 Object, not Node)."""

from __future__ import annotations

from typing import Optional

from jvspatial.core import Object
from jvspatial.core.annotations import attribute, compound_index


@compound_index(
    [("created_at", -1)],
    name="mail_log_created_at",
)
@compound_index(
    [("to", 1), ("created_at", -1)],
    name="mail_log_to_created_at",
)
@compound_index(
    [("status", 1), ("created_at", -1)],
    name="mail_log_status_created_at",
)
class MailLog(Object):
    """One outbound email attempt recorded at the send_email choke point."""

    to: str = attribute(default="", indexed=True)
    from_email: str = attribute(default="")
    from_name: str = attribute(default="")
    reply_to: Optional[str] = attribute(default=None)
    subject: str = attribute(default="", indexed=True)
    text: str = attribute(default="")
    html: str = attribute(default="")
    provider: str = attribute(default="", indexed=True)
    status: str = attribute(default="sent", indexed=True)  # sent | failed
    error: Optional[str] = attribute(default=None)
    provider_message_id: Optional[str] = attribute(default=None)
    created_at: Optional[str] = attribute(default=None, indexed=True)
