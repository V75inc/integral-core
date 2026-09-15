"""Entitlement — durable paid-App access projection (F3 / I-GRAPH-02).

Object (not Node): workspace-scoped entitlement rows are record-shaped,
append/update keyed by workspace + entitlement_key. Stripe projection lands
later; Phase One is manual grant/revoke only.
"""

from __future__ import annotations

from typing import Optional

from jvspatial.core import Object
from jvspatial.core.annotations import attribute, compound_index


@compound_index(
    [("workspace_id", 1), ("entitlement_key", 1)],
    name="entitlement_ws_key",
    unique=True,
    partial_filter_expression={
        "entity": "Entitlement",
        "context.workspace_id": {"$gt": ""},
        "context.entitlement_key": {"$gt": ""},
    },
)
class Entitlement(Object):
    """One workspace's entitlement for a commercial package key."""

    workspace_id: str = attribute(default="", indexed=True)
    entitlement_key: str = attribute(default="", indexed=True)
    package_slug: str = attribute(default="", indexed=True)
    # active | revoked | expired
    status: str = attribute(default="active", indexed=True)
    source: str = attribute(default="manual")  # manual | stripe (later)
    # Phase One continuity policy (explicit, inspectable).
    on_loss: str = attribute(default="pause")  # pause | disable
    data_access: str = attribute(default="core_generic_read")
    retention: str = attribute(default="retain_until_uninstall")
    expires_at: Optional[str] = attribute(default=None)
    revoked_at: Optional[str] = attribute(default=None)
    created_at: Optional[str] = attribute(default=None)
    updated_at: Optional[str] = attribute(default=None)
    created_by: str = attribute(default="")
