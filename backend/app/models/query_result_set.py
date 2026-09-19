"""Durable QuerySpec result provenance metadata (I-GRAPH-02)."""

from __future__ import annotations

from typing import Any, Dict, List

from jvspatial.core import Object
from jvspatial.core.annotations import attribute
from pydantic import Field


class QueryResultSet(Object):
    """Record-shaped provenance for a successful bounded query execution."""

    result_set_id: str = attribute(default="", indexed=True)
    run_id: str = attribute(default="", indexed=True)
    principal_id: str = attribute(default="", indexed=True)
    workspace_id: str = attribute(default="", indexed=True)
    idempotency_key: str = attribute(default="", indexed=True)
    plan_fingerprint: str = attribute(default="", indexed=True)
    normalized_plan: Dict[str, Any] = Field(default_factory=dict)
    graph_revision: str = ""
    item_provenance: List[Dict[str, str]] = Field(default_factory=list)
    item_fingerprints: List[str] = Field(default_factory=list)
    redaction_state: str = "none"
    created_at: str = ""
    expires_at: str = ""


__all__ = ["QueryResultSet"]
