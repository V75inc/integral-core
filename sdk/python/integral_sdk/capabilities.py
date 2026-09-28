"""SDK capability / query typing surface (ADR-012)."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, NotRequired, Optional, Protocol, TypedDict, runtime_checkable


AggregateOp = Literal["count", "sum", "avg", "min", "max", "distinct"]


class AggregateSpec(TypedDict):
    """Input shape for an App-declared aggregate query.

    The App declares a query and computes its own authorized domain rows. Core's
    open-class ``integral_aggregate`` uses the same operation semantics.
    """

    op: AggregateOp
    field: NotRequired[str]
    group_by: NotRequired[str]
    timezone: NotRequired[str]
    scale: NotRequired[int]
    budget: NotRequired[int]


class AggregateGroup(TypedDict):
    key: str
    value: int | str | None
    display: str | None
    count: int


class AggregateResult(TypedDict):
    """Successful exact aggregate output; refusals contain ``error`` instead."""

    op: AggregateOp
    field: str | None
    group_by: str | None
    value: int | str | None
    display: str | None
    currency: str | None
    count: int
    nulls_ignored: int
    groups: List[AggregateGroup]
    complete: Literal[True]


@runtime_checkable
class ToolContextV2(Protocol):
    """Preferred extension facade — query / get / invoke over raw Nodes."""

    user_id: str
    workspace_id: str
    scope: str
    bundle_slug: str

    async def get(self, object_ref: dict) -> Optional[dict]: ...

    async def query(self, query_spec: dict) -> dict: ...

    async def invoke(self, operation_key: str, payload: Optional[dict] = None) -> dict: ...


# Re-export shape aliases for App authors documenting contracts
QueryMode = Literal["declared_capability", "core_open"]
