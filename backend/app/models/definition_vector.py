"""Content-addressed metadata derivations, outside graph authority (I-GRAPH-02)."""

from jvspatial.core import Object
from pydantic import Field

from app.models.indexing import attribute


class DefinitionVector(Object):
    """Immutable derived values only; never a resource inventory or access grant."""

    space: str = ""
    digest: str = ""
    vector: list[float] = Field(default_factory=list)
    created_at: str = attribute(default="", indexed=True)
