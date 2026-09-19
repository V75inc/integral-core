"""CatalogueGeneration — durable workspace capability catalogue (I-GRAPH-02)."""

from __future__ import annotations

from typing import ClassVar, Optional

from jvspatial.core import Object
from jvspatial.core.annotations import attribute


class CatalogueGeneration(Object):
    """Persisted compiled capability catalogue generation per workspace."""

    __entity_name__: ClassVar[Optional[str]] = "CatalogueGeneration"

    workspace_id: str = attribute(default="", indexed=True)
    generation_id: str = attribute(default="", indexed=True)
    package_digests_json: str = ""
    descriptors_json: str = ""
    diagnostics_json: str = ""
    status: str = attribute(default="active", indexed=True)  # active|superseded|failed
    compiled_at: Optional[str] = None
    activated_at: Optional[str] = None
