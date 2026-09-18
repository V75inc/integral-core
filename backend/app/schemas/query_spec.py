"""Bounded QuerySpec request and result contracts."""

from __future__ import annotations

import json
from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.schemas.capability_broker import ReceiptRef

QueryIdentifier = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
QueryCursor = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2048),
]
QueryResource = Literal["entry", "track", "app"]
QueryOperator = Literal[
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "not_in",
    "contains",
    "is_null",
]

QUERY_RESOURCE_FIELDS = {
    "entry": frozenset(
        {
            "id",
            "type_id",
            "title",
            "author_id",
            "track_id",
            "tags",
            "custom_fields",
            "status",
            "body",
            "attachment_ids",
            "visibility",
            "created_at",
            "updated_at",
        }
    ),
    "track": frozenset(
        {
            "id",
            "title",
            "owner_id",
            "purpose",
            "icon",
            "accent_color",
            "visibility",
            "template_id",
            "workspace_id",
            "kind",
            "created_at",
            "updated_at",
        }
    ),
    "app": frozenset(
        {
            "id",
            "name",
            "owner_user_id",
            "description",
            "visibility",
            "workspace_id",
            "accent_color",
            "position",
            "created_at",
            "updated_at",
            "lifecycle_state",
            "version",
        }
    ),
}

QUERY_RESOURCE_EDGES = {
    "entry": {
        "track": ("track", "in"),
        "references": ("entry", "out"),
        "anchored_tracks": ("track", "out"),
    },
    "track": {
        "app": ("app", "in"),
        "entries": ("entry", "out"),
    },
    "app": {"tracks": ("track", "out")},
}


class QueryFilter(BaseModel):
    """One field predicate in a QuerySpec."""

    field: QueryIdentifier
    op: QueryOperator
    value: Any = None

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_bounded_json_value(self) -> "QueryFilter":
        def json_depth(value: Any) -> int:
            if value is None or isinstance(value, (str, int, float, bool)):
                return 0
            if isinstance(value, list):
                return 1 + max((json_depth(item) for item in value), default=0)
            if isinstance(value, dict) and all(isinstance(key, str) for key in value):
                return 1 + max((json_depth(item) for item in value.values()), default=0)
            raise ValueError("filter value must be JSON-compatible")

        if self.op in {"in", "not_in"} and (
            not isinstance(self.value, list) or len(self.value) > 100
        ):
            raise ValueError(f"{self.op} filter value must be a list of at most 100")
        if self.op == "is_null" and not isinstance(self.value, bool):
            raise ValueError("is_null filter value must be a boolean")
        if json_depth(self.value) > 4:
            raise ValueError("filter value nesting depth exceeds 4")
        try:
            encoded = json.dumps(
                self.value,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("filter value must be JSON-compatible") from exc
        if len(encoded) > 8192:
            raise ValueError("filter value exceeds 8192 encoded bytes")
        return self


class QuerySort(BaseModel):
    """One ordered field in a QuerySpec."""

    field: QueryIdentifier
    direction: Literal["asc", "desc"] = "asc"

    model_config = {"extra": "forbid"}


class QueryTraversal(BaseModel):
    """A single outbound or inbound graph hop."""

    edge: QueryIdentifier
    select: List[QueryIdentifier] = Field(default_factory=list, max_length=12)
    direction: Literal["out", "in"] = "out"
    depth: Literal[1] = 1
    limit: int = Field(default=20, ge=1, le=20, strict=True)

    model_config = {"extra": "forbid"}


class QuerySpec(BaseModel):
    """Broker-governed, statically bounded graph query."""

    resource: QueryResource
    select: List[QueryIdentifier] = Field(min_length=1, max_length=20)
    filters: List[QueryFilter] = Field(default_factory=list, max_length=8)
    sort: List[QuerySort] = Field(default_factory=list, max_length=2)
    traversal: List[QueryTraversal] = Field(default_factory=list, max_length=1)
    limit: int = Field(default=20, ge=1, le=100, strict=True)
    cost_ceiling: int = Field(default=100, ge=1, le=1000, strict=True)
    cursor: Optional[QueryCursor] = None

    model_config = {"extra": "forbid"}


def validate_query_spec_semantics(spec: QuerySpec) -> None:
    """Reject fields, edges, and directions outside the Core query contract."""
    allowed_fields = QUERY_RESOURCE_FIELDS[spec.resource]
    for field_name in spec.select:
        if field_name not in allowed_fields:
            raise ValueError(f"field '{field_name}' is not allowed for {spec.resource}")
    for query_filter in spec.filters:
        if query_filter.field not in allowed_fields:
            raise ValueError(
                f"field '{query_filter.field}' is not allowed for {spec.resource}"
            )
    for query_sort in spec.sort:
        if query_sort.field not in allowed_fields:
            raise ValueError(
                f"field '{query_sort.field}' is not allowed for {spec.resource}"
            )
    for traversal in spec.traversal:
        edge = QUERY_RESOURCE_EDGES[spec.resource].get(traversal.edge)
        if edge is None:
            raise ValueError(
                f"edge '{traversal.edge}' is not allowed for {spec.resource}"
            )
        target_resource, fixed_direction = edge
        if traversal.direction != fixed_direction:
            raise ValueError(
                f"direction '{traversal.direction}' is not allowed for edge "
                f"'{traversal.edge}'; expected '{fixed_direction}'"
            )
        for field_name in traversal.select:
            if field_name not in QUERY_RESOURCE_FIELDS[target_resource]:
                raise ValueError(
                    f"field '{field_name}' is not allowed for {target_resource}"
                )


class QueryItemProvenance(BaseModel):
    """Stable identity metadata for one returned item."""

    item_id: str
    resource: QueryResource
    fingerprint: str

    model_config = {"extra": "forbid"}


class QuerySpecResult(BaseModel):
    """Live rows plus durable plan and provenance metadata."""

    items: Optional[List[Dict[str, Any]]] = None
    replayed: bool = False
    result_set_id: str
    normalized_plan: Dict[str, Any]
    graph_revision: str
    item_provenance: List[QueryItemProvenance]
    redaction_state: Literal["none", "fields_redacted"]
    next_cursor: Optional[str] = None
    receipt: Optional[ReceiptRef] = None

    model_config = {"extra": "forbid"}


__all__ = [
    "QueryFilter",
    "QueryItemProvenance",
    "QuerySort",
    "QuerySpec",
    "QuerySpecResult",
    "QueryTraversal",
    "QUERY_RESOURCE_EDGES",
    "QUERY_RESOURCE_FIELDS",
    "validate_query_spec_semantics",
]
