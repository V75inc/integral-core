"""Dashboard widget spec validation and normalization (no service graph deps)."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from app.views import dashboard_widget_types as dwt


def _widget_spec_dict(w: Any) -> Dict[str, Any]:
    if isinstance(w, dict):
        return dict(w)
    if hasattr(w, "model_dump"):
        return w.model_dump()
    return dict(w)


def _chart_line_group_by_error(spec: Dict[str, Any]) -> Optional[str]:
    wtype = str(spec.get("type") or "").strip()
    if wtype != "chart_line":
        return None
    ds = spec.get("data_source") or {}
    group_by = ds.get("group_by")
    aggregate_date = (
        ds.get("kind") == "aggregate"
        and isinstance(group_by, str)
        and (group_by == "date" or group_by.startswith("date:"))
    )
    if (
        group_by is not None
        and str(group_by).strip()
        and group_by != "date"
        and not aggregate_date
    ):
        return (
            f"chart_line requires group_by 'date' (got {group_by!r}); "
            "use chart_bar or chart_pie for categorical breakdowns"
        )
    return None


def _aggregate_data_source_error(spec: Dict[str, Any]) -> Optional[str]:
    data_source = spec.get("data_source") or {}
    if spec.get("type") == "record_summary":
        config = spec.get("config") or {}
        if not isinstance(config, dict):
            return "record_summary config must be an object"
        if data_source.get("kind") != "declared_query":
            return "record_summary requires a declared query data source"
        fields = config.get("fields")
        if not isinstance(fields, list) or not 1 <= len(fields) <= 8:
            return "record_summary requires 1 to 8 field descriptors"
        seen = set()
        for field in fields:
            if not isinstance(field, dict):
                return "record_summary field descriptor must be an object"
            path = field.get("field")
            label = field.get("label")
            if (
                not isinstance(path, str)
                or not path
                or len(path) > 160
                or any(
                    not part.isidentifier() or part.startswith("_")
                    for part in path.split(".")
                )
                or path in seen
                or not isinstance(label, str)
                or not label.strip()
                or len(label) > 80
                or not isinstance(field.get("detail", False), bool)
                or set(field) - {"field", "label", "detail", "value_labels"}
            ):
                return "record_summary field descriptor is invalid or duplicated"
            seen.add(path)
            labels = field.get("value_labels", {})
            if (
                not isinstance(labels, dict)
                or len(labels) > 20
                or any(
                    not isinstance(key, str)
                    or len(key) > 80
                    or not isinstance(value, str)
                    or len(value) > 80
                    for key, value in labels.items()
                )
            ):
                return "record_summary value labels must be bounded text mappings"
        limit = config.get("max_records", 3)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 10
        ):
            return "record_summary max_records must be an integer from 1 to 10"
        empty = config.get("empty_message", "No records yet")
        if not isinstance(empty, str) or len(empty) > 300:
            return "record_summary empty_message must be text up to 300 characters"
        if set(config) - {"fields", "max_records", "empty_message"}:
            return "record_summary config contains unsupported fields"
    if data_source.get("kind") == "declared_query":
        if not str(data_source.get("query_key") or "").strip():
            return "declared query data source requires query_key"
        for key in ("rows_path", "total_path"):
            if not str(data_source.get(key) or "").strip():
                return f"declared query data source requires {key}"
        if not isinstance(data_source.get("query_params", {}), dict):
            return "declared query data source query_params must be an object"
    if spec.get("type") == "progress":
        target = (spec.get("config") or {}).get("target")
        if (
            not isinstance(target, (int, float))
            or isinstance(target, bool)
            or target <= 0
        ):
            return "progress widget requires a positive numeric config.target"
        if data_source.get("kind") != "aggregate":
            return "progress widget requires an aggregate data source"
    if data_source.get("kind") != "aggregate":
        return None
    op = str(data_source.get("op") or "count")
    if op not in {"count", "sum", "avg", "min", "max", "distinct"}:
        return f"aggregate data source has unsupported op {op!r}"
    if (
        op in {"sum", "avg", "min", "max", "distinct"}
        and not str(data_source.get("field") or "").strip()
    ):
        return f"aggregate op {op!r} requires a field"
    budget = data_source.get("budget", 5000)
    if (
        not isinstance(budget, int)
        or isinstance(budget, bool)
        or not 1 <= budget <= 5000
    ):
        return "aggregate budget must be an integer from 1 to 5000"
    scale = data_source.get("scale")
    if scale is not None and (
        not isinstance(scale, int) or isinstance(scale, bool) or not 0 <= scale <= 8
    ):
        return "aggregate scale must be an integer from 0 to 8"
    return None


def validate_widget_specs(raw: Optional[List[Any]]) -> List[str]:
    """Return human-readable validation errors for widget specs (pre-persist)."""
    if not raw:
        return []
    errors: List[str] = []
    valid_types = ", ".join(sorted(dwt.allowed_keys()))
    for idx, w in enumerate(raw):
        spec = _widget_spec_dict(w)
        wtype = str(spec.get("type") or "").strip()
        if not wtype:
            errors.append(f"widget[{idx}]: missing type (valid: {valid_types})")
            continue
        if not dwt.validate_widget_type(wtype):
            errors.append(
                f"widget[{idx}]: unknown type {wtype!r} (valid: {valid_types})"
            )
            continue
        line_err = _chart_line_group_by_error(spec)
        if line_err:
            errors.append(f"widget[{idx}]: {line_err}")
        aggregate_err = _aggregate_data_source_error(spec)
        if aggregate_err:
            errors.append(f"widget[{idx}]: {aggregate_err}")
    return errors


def normalize_widget_specs(
    raw: Optional[List[Any]],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Normalize widget specs and report any that were dropped."""
    if not raw:
        return [], []
    out: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    for idx, w in enumerate(raw):
        spec = _widget_spec_dict(w)
        wtype = str(spec.get("type") or "").strip()
        if not wtype or not dwt.validate_widget_type(wtype):
            dropped.append(
                {
                    "index": idx,
                    "type": wtype or "(missing)",
                    "reason": "unknown_widget_type",
                }
            )
            continue
        line_err = _chart_line_group_by_error(spec)
        if line_err:
            dropped.append(
                {
                    "index": idx,
                    "type": wtype,
                    "reason": "invalid_data_source",
                    "detail": line_err,
                }
            )
            continue
        aggregate_err = _aggregate_data_source_error(spec)
        if aggregate_err:
            dropped.append(
                {
                    "index": idx,
                    "type": wtype,
                    "reason": "invalid_data_source",
                    "detail": aggregate_err,
                }
            )
            continue
        wid = str(spec.get("id") or "").strip() or f"w_{uuid.uuid4().hex[:8]}"
        grid = spec.get("grid") or {}
        data_source = dict(spec.get("data_source") or {})
        if wtype == "chart_line" and not data_source.get("group_by"):
            data_source["group_by"] = "date"
        out.append(
            {
                "id": wid,
                "type": wtype,
                "title": str(spec.get("title") or ""),
                "grid": {
                    "x": int(grid.get("x", 0)),
                    "y": int(grid.get("y", 0)),
                    "w": int(grid.get("w", 4)),
                    "h": int(grid.get("h", 3)),
                },
                "config": dict(spec.get("config") or {}),
                "data_source": data_source,
            }
        )
    return out, dropped
