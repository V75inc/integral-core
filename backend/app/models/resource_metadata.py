"""Derived metadata rows, never graph participants or authority (I-GRAPH-02)."""

from jvspatial.core import Object

from app.models.indexing import attribute


class ResourceMetadataProjection(Object):
    """Scalar-keyed App/Track hints with a workspace-selective storage index."""

    workspace_id: str = attribute(default="", indexed=True)
    source_id: str = ""
    kind: str = ""
    name: str = ""
    description: str = ""
    lifecycle: str = ""
    parent_app_id: str | None = None
    source_digest: str = ""
