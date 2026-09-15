"""Dashboard widget-type registry for app-scoped analytics dashboards.

Mirrors the content-profile view palette pattern but for dashboard tiles
that aggregate data across an app's tracks. Agents compose dashboards by
picking ``widget_type`` keys and supplying ``config`` + ``data_source``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

_CONTRACTS_DIR = Path(__file__).resolve().parent / "contracts"


@dataclass(frozen=True)
class DashboardWidgetSpec:
    type: str
    config_schema: Dict[str, Any] = field(default_factory=dict)
    data_source_schema: Dict[str, Any] = field(default_factory=dict)
    source: str = "builtin"
    label: str = ""
    description: str = ""
    palette_group: str = "general"
    configurable: bool = True
    default_size: Dict[str, int] = field(default_factory=lambda: {"w": 4, "h": 3})


_REGISTRY: Dict[str, DashboardWidgetSpec] = {}
_REGISTRY_VERSION: int = 0


def _load_dashboard_contracts() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not _CONTRACTS_DIR.is_dir():
        return out
    for path in sorted(_CONTRACTS_DIR.glob("dashboard_*.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        key = str(row.get("type") or "").strip()
        if key:
            out[key] = row
    return out


_CONTRACTS_BY_TYPE: Dict[str, Dict[str, Any]] = _load_dashboard_contracts()


def _with_contract_defaults(spec: DashboardWidgetSpec) -> DashboardWidgetSpec:
    contract = _CONTRACTS_BY_TYPE.get(spec.type, {})
    if not contract:
        return spec
    default_size = contract.get("default_size") or spec.default_size
    return replace(
        spec,
        label=str(contract.get("label") or spec.label or spec.type),
        description=str(contract.get("description") or spec.description),
        palette_group=str(contract.get("palette_group") or spec.palette_group),
        configurable=bool(contract.get("configurable", spec.configurable)),
        default_size=dict(default_size),
    )


def register_dashboard_widget(
    spec: DashboardWidgetSpec, *, override: bool = False
) -> None:
    """Register a ``DashboardWidgetSpec`` in the global registry."""
    global _REGISTRY_VERSION
    key = spec.type.strip()
    if not key:
        raise ValueError("DashboardWidgetSpec.type is required")
    if key in _REGISTRY and not override:
        return
    _REGISTRY[key] = _with_contract_defaults(spec)
    _REGISTRY_VERSION += 1


def allowed_keys() -> Iterable[str]:
    """Return registered dashboard widget type keys."""
    return _REGISTRY.keys()


def get_spec(widget_type: str) -> Optional[DashboardWidgetSpec]:
    """Return the spec for ``widget_type`` or ``None`` if unknown."""
    return _REGISTRY.get(widget_type)


def iter_specs() -> Iterable[DashboardWidgetSpec]:
    """Return all registered dashboard widget specs."""
    return _REGISTRY.values()


def registry_version() -> int:
    """Return a monotonic counter for cache invalidation."""
    return _REGISTRY_VERSION


def validate_widget_type(widget_type: str) -> bool:
    """Return True when ``widget_type`` is registered."""
    return widget_type in _REGISTRY


def _register_builtins() -> None:
    builtins = [
        DashboardWidgetSpec(
            type="metric_card",
            label="Metric",
            description="Single KPI number.",
            palette_group="metrics",
            config_schema={"properties": {"suffix": {"type": "string"}}},
            data_source_schema={
                "properties": {
                    "kind": {"enum": ["count", "count_filtered"]},
                    "track_id": {"type": "string"},
                }
            },
        ),
        DashboardWidgetSpec(
            type="metric_row",
            label="Metric row",
            description="Row of compact KPIs.",
            palette_group="metrics",
            data_source_schema={
                "properties": {
                    "metrics": {"type": "array", "items": {"type": "object"}}
                }
            },
        ),
        DashboardWidgetSpec(
            type="chart_bar",
            label="Bar chart",
            description="Bar chart from grouped counts.",
            palette_group="charts",
            config_schema={
                "properties": {
                    "orientation": {"enum": ["vertical", "horizontal"]},
                    "show_legend": {"type": "boolean"},
                }
            },
            data_source_schema={
                "properties": {
                    "kind": {"enum": ["grouped_count"]},
                    "group_by": {"type": "string"},
                }
            },
        ),
        DashboardWidgetSpec(
            type="chart_line",
            label="Line chart",
            description="Trend over time.",
            palette_group="charts",
            data_source_schema={
                "properties": {
                    "kind": {"enum": ["grouped_count"]},
                    "group_by": {"enum": ["date"]},
                }
            },
        ),
        DashboardWidgetSpec(
            type="chart_pie",
            label="Pie chart",
            description="Donut/pie from grouped counts.",
            palette_group="charts",
            data_source_schema={
                "properties": {
                    "kind": {"enum": ["grouped_count"]},
                    "group_by": {"type": "string"},
                }
            },
        ),
        DashboardWidgetSpec(
            type="activity_digest",
            label="Activity digest",
            description="Per-track activity summary.",
            palette_group="summaries",
            data_source_schema={
                "properties": {
                    "kind": {"enum": ["activity_digest"]},
                    "period": {"enum": ["today", "week", "month"]},
                }
            },
        ),
        DashboardWidgetSpec(
            type="recent_entries",
            label="Recent entries",
            description="Latest entries across app tracks.",
            palette_group="summaries",
            data_source_schema={"properties": {"limit": {"type": "integer"}}},
        ),
        DashboardWidgetSpec(
            type="track_breakdown",
            label="Track breakdown",
            description="Tracks with entry counts.",
            palette_group="summaries",
            data_source_schema={"properties": {"kind": {"enum": ["track_breakdown"]}}},
        ),
    ]
    for spec in builtins:
        register_dashboard_widget(spec, override=True)


_register_builtins()
