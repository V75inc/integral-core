"""Host-controlled limits for model-facing workspace graph context."""

from typing import Dict, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

GraphEdge = Literal["CONTAINS", "CATALOGS", "REFERENCES", "Anchors", "IS_OF_TYPE"]


class GraphContextRequest(BaseModel):
    """Select a bounded neighborhood; identity, fields and scope stay server-owned."""

    model_config = ConfigDict(extra="forbid")
    root_id: Optional[str] = Field(default=None, min_length=1, max_length=512)
    view: Literal["relationships", "hierarchy", "ontology"] = "ontology"
    detail: Literal["summary", "recorded"] = "summary"
    direction: Literal["out", "in", "both"] = "both"
    edge_types: Tuple[GraphEdge, ...] = Field(
        default=("CONTAINS", "CATALOGS", "REFERENCES", "Anchors", "IS_OF_TYPE"),
        max_length=5,
    )
    max_depth: int = Field(default=3, ge=0, le=4)
    max_nodes: int = Field(default=60, ge=1, le=150)
    max_edges: int = Field(default=100, ge=0, le=300)
    max_candidates: int = Field(default=500, ge=1, le=2000)
    max_edges_per_node: int = Field(default=100, ge=1, le=300)
    max_bytes: int = Field(default=24576, ge=4096, le=65536)


class RecordedField(BaseModel):
    """Hints and same-record evidence keys for a selected scalar field."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    label: Optional[str] = Field(default=None, max_length=128)
    description: Optional[str] = Field(default=None, max_length=512)
    unit: Optional[str] = Field(default=None, max_length=64)
    source: Optional[str] = None
    observed_at: Optional[str] = None
    verification: Optional[str] = None
    confidence: Optional[str] = None


class RecordedRelation(BaseModel):
    """Author-declared meaning, never operational verification."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    description: str = Field(min_length=1, max_length=512)
    direction: str = Field(min_length=1, max_length=128)
    inverse: Optional[str] = Field(default=None, max_length=128)
    conditions: Optional[str] = Field(default=None, max_length=512)


class EntryGraphContext(BaseModel):
    """Opt-in Operational Model context contract, qualified by record revision."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_revision: int = Field(ge=1)
    fields: Dict[str, RecordedField] = Field(default_factory=dict, max_length=12)
    relations: Dict[str, RecordedRelation] = Field(default_factory=dict, max_length=8)

    @model_validator(mode="after")
    def validate_keys(self):
        import re

        keys = [*self.fields, *self.relations]
        keys += [
            value
            for field in self.fields.values()
            for value in (
                field.source,
                field.observed_at,
                field.verification,
                field.confidence,
            )
            if value is not None
        ]
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key) for key in keys):
            raise ValueError("Context keys must be exact scalar field keys")
        if any(
            key not in self.fields
            for key in keys[len(self.fields) + len(self.relations) :]
        ):
            raise ValueError("Evidence keys must themselves be selected")
        return self


def validate_entry_graph_context(value, fields):
    """Reject hidden, undeclared and non-scalar fields before persistence."""
    config = EntryGraphContext.model_validate(value)
    declared = {field["key"]: field for field in fields}
    scalar_types = {
        "text",
        "number",
        "date",
        "datetime",
        "boolean",
        "select",
        "url",
        "email",
    }
    for key in config.fields:
        field = declared.get(key, {})
        if field.get("hidden") or field.get("type") not in scalar_types:
            raise ValueError("Context requires visible declared scalar fields")
    for key in config.relations:
        field = declared.get(key, {})
        if field.get("hidden") or field.get("type") != "relation":
            raise ValueError(
                "Context meanings require visible declared relation fields"
            )
    return config
