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
    raw: Any,
    queries: List[Dict[str, Any]],
    operations: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Validate bounded home content; no graph writes or inferred data sources."""
    if raw is None or raw == {}:
        return None
    from app.schemas.app_home import AppHomeComposition

    raw = AppHomeComposition.model_validate(raw).model_dump(
        mode="json", exclude_none=True
    )
    if raw["renderer"] == "native":
        validate_home_components(raw, queries=queries, operations=operations or [])
        return raw
    widgets = raw["widgets"]
    declarations = {item["key"]: item for item in queries}
    normalized = []
    seen = set()
    occupied: set[tuple[int, int]] = set()
    for raw_widget in widgets:
        if raw_widget["type"] == "native_component":
            normalized.append(raw_widget)
            continue
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
    raw["widgets"] = normalized
    validate_home_components(raw, queries=queries, operations=operations or [])
    return raw


def validate_home_components(
    home: Dict[str, Any],
    *,
    queries: Optional[List[Dict[str, Any]]] = None,
    operations: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Compatibility entry point for definition-upgrade presentation checks."""
    from app.services.home_native import validate_native_home

    validate_native_home(
        home, {"app": {"queries": queries or [], "operations": operations or []}}
    )
