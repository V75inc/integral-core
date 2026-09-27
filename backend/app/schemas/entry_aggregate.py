"""Typed shape for a governed entry aggregate (W3.1)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

AggregateOp = Literal["count", "sum", "avg", "min", "max", "distinct"]


class AggregateSpec(BaseModel):
    """What to compute. ``field`` is a custom-field key or a platform key."""

    op: AggregateOp
    field: str = ""
    group_by: str = ""
    timezone: str = "UTC"
    scale: Optional[int] = None
    budget: int = Field(default=5000, ge=1)
