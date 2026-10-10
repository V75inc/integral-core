"""Compile a package-owned home from explicit authorized query/widget contracts."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.schemas.dashboards import DashboardWidgetSpec
from app.services.dashboard_widget_validation import validate_widget_specs


def _schema_path(schema: Dict[str, Any], path: str) -> Dict[str, Any]:
    current = schema
    for key in path.split("."):
        current = (current.get("properties") or {}).get(key) or {}
    return current


def normalize_app_home(
    raw: Any, queries: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Validate bounded home content; no graph writes or inferred data sources."""
    if raw is None or raw == {}:
        return None
    if not isinstance(raw, dict) or set(raw) - {
        "title",
        "description",
        "widgets",
        "actions",
    }:
        raise ValueError("app.home contains unsupported fields")
    title = raw.get("title")
    description = raw.get("description", "")
    if not isinstance(title, str) or not title.strip() or len(title) > 100:
        raise ValueError("app.home.title must contain 1 to 100 characters")
    if not isinstance(description, str) or len(description) > 500:
        raise ValueError("app.home.description must be text up to 500 characters")
    widgets = raw.get("widgets")
    if not isinstance(widgets, list) or not 1 <= len(widgets) <= 6:
        raise ValueError("app.home requires 1 to 6 widgets")
    declarations = {item["key"]: item for item in queries}
    normalized = []
    seen = set()
    occupied: set[tuple[int, int]] = set()
    for raw_widget in widgets:
        widget = DashboardWidgetSpec.model_validate(raw_widget).model_dump()
        identity = widget["id"]
        if not identity or len(identity) > 128 or identity in seen:
            raise ValueError("app.home widget IDs must be unique and bounded")
        seen.add(identity)
        errors = validate_widget_specs([widget])
        if errors:
            raise ValueError("app.home invalid widget: " + errors[0])
        source = widget["data_source"]
        query = declarations.get(source.get("query_key"))
        contract = (query or {}).get("dashboard")
        if source.get("kind") != "declared_query" or not isinstance(contract, dict):
            raise ValueError(
                "app.home widgets require an owning dashboard-enabled declared query"
            )
        if any(
            source.get(key) != contract.get(key) for key in ("rows_path", "total_path")
        ) or source.get("query_params", {}) != contract.get("params", {}):
            raise ValueError("app.home widget must retain its declared query contract")
        if widget["type"] == "record_summary":
            rows_schema = _schema_path(
                query.get("output_schema") or {}, source["rows_path"]
            )
            item_schema = rows_schema.get("items") or {}
            for field in widget["config"]["fields"]:
                field_type = _schema_path(item_schema, field["field"]).get("type")
                types = (
                    set(field_type) if isinstance(field_type, list) else {field_type}
                )
                if not types - {"null"} or not types <= {
                    "string",
                    "boolean",
                    "integer",
                    "number",
                    "null",
                }:
                    raise ValueError(
                        "app.home summary field must be explicitly declared as scalar"
                    )
        grid = widget["grid"]
        if any(isinstance(grid[k], bool) for k in ("x", "y", "w", "h")) or not (
            0 <= grid["x"] < 12
            and 0 <= grid["y"] <= 100
            and 1 <= grid["w"] <= 12
            and 1 <= grid["h"] <= 20
            and grid["x"] + grid["w"] <= 12
        ):
            raise ValueError("app.home widget placement is out of bounds")
        cells = {
            (x, y)
            for x in range(grid["x"], grid["x"] + grid["w"])
            for y in range(grid["y"], grid["y"] + grid["h"])
        }
        if cells & occupied:
            raise ValueError("app.home widget placements must not overlap")
        occupied.update(cells)
        normalized.append(widget)
    actions = raw.get("actions", [])
    if not isinstance(actions, list) or len(actions) > 3:
        raise ValueError("app.home supports at most 3 draft-chat actions")
    action_labels = set()
    for action in actions:
        if (
            not isinstance(action, dict)
            or not {"label", "draft"} <= set(action)
            or set(action) - {"label", "draft", "when"}
        ):
            raise ValueError("app.home action requires label and draft")
        if (
            not isinstance(action["label"], str)
            or not action["label"].strip()
            or len(action["label"]) > 80
            or not isinstance(action["draft"], str)
            or not action["draft"].strip()
            or len(action["draft"]) > 500
        ):
            raise ValueError("app.home action text is empty or out of bounds")
        if action["label"] in action_labels:
            raise ValueError("app.home action labels must be unique")
        action_labels.add(action["label"])
        condition = action.get("when")
        if condition is not None and (
            not isinstance(condition, dict)
            or set(condition) != {"widget", "state"}
            or condition.get("widget") not in seen
            or condition.get("state") not in {"empty", "has_records"}
        ):
            raise ValueError(
                "app.home action condition must name a widget and empty/has_records state"
            )
    return {
        "title": title.strip(),
        "description": description,
        "widgets": normalized,
        "actions": actions,
    }
