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
    if group_by is not None and str(group_by).strip() and group_by != "date":
        return (
            f"chart_line requires group_by 'date' (got {group_by!r}); "
            "use chart_bar or chart_pie for categorical breakdowns"
        )
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
