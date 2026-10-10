"""Durable open build batch (I-GRAPH-02). Lost when the process restarts otherwise."""

from __future__ import annotations

from typing import Any, Dict, List

from jvspatial.core import Object
from jvspatial.core.annotations import attribute
from pydantic import Field

BATCH_SCHEMA_REVISION = "w6.2-1"


class OpenBuildBatch(Object):
    """Uncommitted batch ops for one user and session."""

    user_id: str = attribute(default="", indexed=True)
    session_id: str = attribute(default="", indexed=True)
    schema_revision: str = ""
    label: str = ""
    ops: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: str = ""
    auto_continuation_attempts: int = 0
