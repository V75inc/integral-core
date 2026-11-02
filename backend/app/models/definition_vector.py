"""Content-addressed metadata derivations, outside graph authority (I-GRAPH-02)."""

from jvspatial.core import Object
from jvspatial.core.annotations import attribute
from pydantic import Field


class DefinitionVector(Object):
    """Immutable derived values only; never a resource inventory or access grant."""

    space: str = ""
    digest: str = ""
    vector: list[float] = Field(default_factory=list)
    created_at: str = attribute(default="", indexed=True)
