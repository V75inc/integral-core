"""Per-workspace transactional email delivery config (I-GRAPH-02 Object)."""

from __future__ import annotations

from typing import Optional

from jvspatial.core import Object
from jvspatial.core.annotations import attribute, compound_index


@compound_index(
    [("workspace_id", 1)],
    name="workspace_email_delivery_ws_id",
    unique=True,
    partial_filter_expression={
        "entity": "WorkspaceEmailDelivery",
        "context.workspace_id": {"$gt": ""},
    },
)
class WorkspaceEmailDelivery(Object):
    """Encrypted Resend/SendGrid credentials and default From for one workspace."""

    workspace_id: str = attribute(default="", indexed=True)
    provider: str = attribute(default="", indexed=True)  # resend | sendgrid
    from_email: str = attribute(default="")
    from_name: str = attribute(default="")
    reply_to: Optional[str] = attribute(default=None)
    api_key_enc: str = attribute(default="")
    key_fingerprint: str = attribute(default="")
    is_enabled: bool = attribute(default=False)
    validated_at: Optional[str] = attribute(default=None)
    last_used_at: Optional[str] = attribute(default=None)
    last_error: Optional[str] = attribute(default=None)
    created_at: Optional[str] = attribute(default=None)
    updated_at: Optional[str] = attribute(default=None)
