"""Workspace-owned App Home composition through ordinary App update approval."""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
    try:
        normalized = AppHomeComposition.model_validate(home).model_dump(
            mode="json", exclude_none=True
        )
    except ValidationError as exc:
        # Pydantic's str(exc) includes input_value, potentially private record
        # data. Only canonical schema field names and numeric indices may leave
        # this boundary; extra keys and custom validator text are untrusted.
        known_fields = _home_schema_fields()
        issues = []
        for error in exc.errors(include_input=False, include_context=False)[:8]:
            path = ".".join(
                (
                    str(segment)
                    if isinstance(segment, int) or segment in known_fields
                    else "<extra field>"
                )
                for segment in error["loc"]
            )
            reason = {
                "missing": "required field",
                "string_type": "expected string",
                "int_type": "expected integer",
                "int_parsing": "expected integer",
                "list_type": "expected array",
                "dict_type": "expected object",
                "extra_forbidden": "unsupported field",
                "too_short": "too few items or characters",
                "too_long": "too many items or characters",
            }.get(error["type"], "value does not match the declared schema")
            issues.append(f"{path or 'home'}: {reason}")
        raise ValueError("Invalid Home arguments: " + "; ".join(issues)) from exc
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


def _home_schema_fields() -> set[str]:
    """Collect schema-owned labels without admitting submitted field names."""
    names: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict):
                names.update(properties)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(AppHomeComposition.model_json_schema())
    return names
