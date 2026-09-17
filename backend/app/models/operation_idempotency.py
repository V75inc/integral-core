"""OperationIdempotencyRecord — durable idempotency for app operations (I-GRAPH-02)."""

from __future__ import annotations

from typing import ClassVar, Optional

from jvspatial.core import Object
from jvspatial.core.annotations import attribute


class OperationIdempotencyRecord(Object):
    """Cached operation result keyed by workspace + app + operation + principal + key."""

    __entity_name__: ClassVar[Optional[str]] = "OperationIdempotencyRecord"

    workspace_id: str = attribute(default="", indexed=True)
    app_id: str = attribute(default="", indexed=True)
    operation_key: str = attribute(default="", indexed=True)
    principal_id: str = attribute(default="", indexed=True)
    idempotency_key: str = attribute(default="", indexed=True)
    request_hash: str = ""
    result_json: str = ""
    created_at: Optional[str] = None
