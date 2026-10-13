"""Workspace-owned App Home composition through ordinary App update approval."""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.dashboards import DashboardWidgetSpec
from app.services.dashboard_service import validate_dashboard_field_bindings
from app.services.dashboard_widget_validation import validate_widget_specs


class AppHomeComposition(BaseModel):
    """Neutral Home content, separate from named analytics dashboards."""

    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    widgets: List[DashboardWidgetSpec] = Field(min_length=1, max_length=12)


async def validate_home_composition(
    *, user_id: str, app_id: str, home: Dict[str, Any]
) -> Dict[str, Any]:
    """Validate shape, placement and current authorized data-source bindings."""
    if home == {}:
        return {}
    normalized = AppHomeComposition.model_validate(home).model_dump(
        mode="json", exclude_none=True
    )
    if not normalized["title"].strip():
        raise ValueError("Home title is required")
    errors = validate_widget_specs(normalized["widgets"])
    if errors:
        raise ValueError("Invalid Home widgets: " + "; ".join(errors))
    ids: set[str] = set()
    placements: List[Dict[str, int]] = []
    for widget in normalized["widgets"]:
        if widget["id"] in ids:
            raise ValueError("Home widget IDs must be unique")
        ids.add(widget["id"])
        g = widget["grid"]
        if not (
            0 <= g["x"] < 12
            and 0 <= g["y"] <= 100
            and 1 <= g["w"] <= 12
            and 1 <= g["h"] <= 12
            and g["x"] + g["w"] <= 12
        ):
            raise ValueError("Home widget placement is out of bounds")
        if any(
            g["x"] < p["x"] + p["w"]
            and p["x"] < g["x"] + g["w"]
            and g["y"] < p["y"] + p["h"]
            and p["y"] < g["y"] + g["h"]
            for p in placements
        ):
            raise ValueError("Home widgets must not overlap")
        placements.append(g)
    await validate_dashboard_field_bindings(
        user_id=user_id, app_id=app_id, widgets=normalized["widgets"]
    )
    return normalized
