"""Governed QuerySpec / QueryResult contracts (ADR-012 QuerySpec v1 = C)."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.schemas.capabilities import Evidence, ObjectRef

QueryMode = Literal["declared_capability", "core_open"]
CoreResource = Literal["entry", "track", "app"]
RetrievalMode = Literal["deterministic", "semantic", "hybrid"]


class FilterExpr(BaseModel):
    field: str
    op: Literal[
        "eq", "neq", "in", "not_in", "contains", "gt", "lt", "gte", "lte", "exists"
    ] = "eq"
    value: Any = None


class QuerySpec(BaseModel):
    """Structured reads through declared App or open Core contracts."""

    mode: QueryMode
    # declared_capability
    capability_key: Optional[str] = None
    app_id: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    # core_open
    resource: Optional[CoreResource] = None
    filters: List[FilterExpr] = Field(default_factory=list)
    projection: List[str] = Field(default_factory=list)
    sort: Optional[str] = None
    cursor: Optional[str] = None
    limit: int = Field(default=50, ge=1, le=200)
    max_depth: int = Field(default=1, ge=0, le=2)
    retrieval_mode: RetrievalMode = Field(
        default="deterministic",
        description=(
            "Structured QuerySpec supports deterministic reads. Semantic/hybrid "
            "values are reserved and explicitly rejected; use the retrieval service "
            "for ranked evidence search."
        ),
    )

    @model_validator(mode="after")
    def _validate_mode_fields(self) -> "QuerySpec":
        if self.mode == "declared_capability":
            if not (self.capability_key or "").strip():
                raise ValueError("capability_key required for declared_capability mode")
        elif self.mode == "core_open":
            if self.resource is None:
                raise ValueError("resource required for core_open mode")
        return self


class QueryResult(BaseModel):
    schema_version: str = "1.0"
    mode: QueryMode
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    result_metadata: Dict[str, Any] = Field(default_factory=dict)
    row_collection: Optional[str] = None
    passages: List[Dict[str, Any]] = Field(default_factory=list)
    object_refs: List[ObjectRef] = Field(default_factory=list)
    evidence: Evidence = Field(default_factory=Evidence)
    cursor: Optional[str] = None
    total_estimate: Optional[int] = None
    warnings: List[str] = Field(default_factory=list)
    degraded: bool = False
    explain: Optional[Dict[str, Any]] = None


def _declared_output(value: dict) -> Optional[Dict[str, Any]]:
    """Reconstruct the validated source envelope without serializing it twice."""
    if value.get("mode") != "declared_capability":
        return None
    rows = value.get("rows") or []
    collection = value.get("row_collection")
    if isinstance(collection, str):
        return {**value.get("result_metadata", {}), collection: rows}
    return rows[0] if len(rows) == 1 and isinstance(rows[0], dict) else None


class SDKQueryResult(dict):
    """The normal query mapping with a lossless, non-JSON SDK view."""

    @property
    def source_output(self) -> Optional[Dict[str, Any]]:
        return _declared_output(self)


class QueryRequest(BaseModel):
    query: QuerySpec


class CapabilitiesListResponse(BaseModel):
    workspace_id: str
    generation_id: str
    capabilities: List[Dict[str, Any]] = Field(default_factory=list)
