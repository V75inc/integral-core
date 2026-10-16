"""Read-only workspace ontology request and safe display shapes."""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class OntologyRequest(BaseModel):
    """Identity, graph vocabulary and field access remain server controlled."""

    model_config = ConfigDict(extra="forbid")
    root_id: Optional[str] = Field(default=None, min_length=1, max_length=512)
    max_depth: int = Field(default=3, ge=1, le=4)


class OntologyField(BaseModel):
    """Schema labels without values, defaults or relation target identifiers."""

    key: str
    label: str
    type: str
    required: bool = False


class OntologyRoot(BaseModel):
    """A currently readable starting point, including disconnected grants."""

    id: str
    entity: Literal["WorkspaceApp", "Track"]
    label: str
