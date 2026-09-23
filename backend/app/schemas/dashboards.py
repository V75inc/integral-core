"""Pydantic schemas for app dashboard CRUD and widget specs."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.governed_query import FilterExpr
from app.services.query_filters import normalize_filter_expressions


class GridPlacement(BaseModel):
    x: int = 0
    y: int = 0
    w: int = 4
    h: int = 2


class DataSourceSpec(BaseModel):
    kind: str = "count"
    track_id: Optional[str] = None
    track_ids: Optional[List[str]] = None
    group_by: Optional[str] = None
    status: Optional[str] = None
    statuses: Optional[List[str]] = None
    tags: Optional[List[str]] = None
    entry_type: Optional[str] = None
    since: Optional[str] = None
    until: Optional[str] = None
    period: Optional[Literal["today", "week", "month", "quarter", "year"]] = None
    limit: Optional[int] = None
    filters: List[FilterExpr] = Field(default_factory=list)
    view_id: Optional[str] = None
    metrics: Optional[List[Dict[str, Any]]] = None
    metric: Optional[str] = None

    @field_validator("filters", mode="before")
    @classmethod
    def _normalize_filters(cls, value: Any) -> List[FilterExpr]:
        return normalize_filter_expressions(value)


class DashboardWidgetSpec(BaseModel):
    id: str
    type: str
    title: str = ""
    grid: GridPlacement = Field(default_factory=GridPlacement)
    config: Dict[str, Any] = Field(default_factory=dict)
    data_source: DataSourceSpec = Field(default_factory=DataSourceSpec)


class DashboardLayoutSpec(BaseModel):
    columns: int = 12
    row_height: int = 80


class DashboardCreateRequest(BaseModel):
    name: str
    layout: Optional[DashboardLayoutSpec] = None
    widgets: Optional[List[DashboardWidgetSpec]] = None
    is_default: bool = False


class DashboardUpdateRequest(BaseModel):
    name: Optional[str] = None
    layout: Optional[DashboardLayoutSpec] = None
    widgets: Optional[List[DashboardWidgetSpec]] = None
    is_default: Optional[bool] = None


class DashboardListResponse(BaseModel):
    dashboards: List[Dict[str, Any]]
    total: int


class DashboardDataResponse(BaseModel):
    widget_data: Dict[str, Any]
