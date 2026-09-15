"""Content profile view-type registry.

Pillar 1 + Pillar 4 of the agent-authorable substrate. Replaces the hardcoded
``VALID_VIEW_TYPES`` set in ``content_profile_runtime`` with an extensible
registry. Built-in widgets (feed/kanban/table/calendar/gallery) and the
composable meta-widgets (composable_list/grid/board/timeline) register at
module import. Plugin widgets register at startup via
:func:`register_view_type`. Manifest-declared composite views are resolved
per-compile via :func:`resolve` and never persisted in the global registry.

The registry today is metadata-first: rendering happens entirely on the
frontend (see ``frontend/src/views/registry.tsx``). The optional
``server_query_helper`` callable on :class:`ViewTypeSpec` reserves the
contract for future server-side query specialization (e.g. semantic ranking
for a ``semantic_list`` view).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from app.views.view_contract_catalog import load_view_contract_catalog


@dataclass(frozen=True)
class ViewTypeSpec:
    """Declarative spec for a content-profile view type / widget capability.

    ``base`` is set for composite views that decorate a primitive widget with
    extra config (e.g. ``roadmap = composable_board + {group_by: stage}``).
    Composite specs are constructed per-compile from manifest ``view_types[]``
    and resolved via :func:`resolve`; they are not registered globally.
    """

    type: str
    base: Optional[str] = None
    config_schema: Dict[str, Any] = field(default_factory=dict)
    server_query_helper: Optional[Callable[..., Awaitable[Any]]] = None
    source: str = "builtin"  # builtin | composite | plugin
    signed: bool = False
    label: str = ""
    description: str = ""
    composite_config: Dict[str, Any] = field(default_factory=dict)
    supported_platforms: List[str] = field(default_factory=lambda: ["web"])
    default_always_on: bool = False
    palette_group: str = "general"
    configurable: bool = True
    hot_loadable: bool = False
    # Where this view type is meaningful for placement purposes — mirrors the
    # frontend's `WidgetRegistration.scope` (`frontend/src/views/types.ts`).
    # "track" (default) — addressable as one of a track's own view-registry
    # entries / tab candidates. "entry" — only meaningful bound inside a
    # specific entry's own `related_views[]` slot. "both" — valid in either
    # placement. See `content_profile_compile.py`'s related_views placement
    # check for the one enforcement point this currently backs.
    scope: str = "track"


_REGISTRY: Dict[str, ViewTypeSpec] = {}
_REGISTRY_VERSION: int = 0
_CONTRACTS_BY_TYPE: Dict[str, Dict[str, Any]] = {
    str(row.get("type") or ""): row for row in load_view_contract_catalog()
}

# UI Packs Standard (docs/content-profiles/UI_PACKS.md) requires every NEW
# plugin-sourced view type to be namespaced as `pack-name/view-name` — see
# `register_view_type` below. These bare names predate the standard and are
# grandfathered so existing installed profiles keep compiling; do not add to
# this set — new plugin view types must use the namespaced form.
_LEGACY_UNNAMESPACED_PLUGIN_VIEW_TYPES = frozenset(
    {
        "payroll_register",
        "editable_table",
        "action_bar",
        "form_region",
        "layout_container",
        "static_content",
        "tree_region",
        "chart_region",
        "summary_tiles",
        "reverse_relation_list",
        "modal_region",
        "popover_region",
        "drawer_region",
    }
)


def _contract_for(type_key: str) -> Dict[str, Any]:
    return _CONTRACTS_BY_TYPE.get(type_key, {})


def _with_contract_defaults(spec: ViewTypeSpec) -> ViewTypeSpec:
    contract = _contract_for(spec.type)
    if not contract:
        return spec
    return replace(
        spec,
        label=str(contract.get("label") or spec.label or spec.type),
        supported_platforms=list(contract.get("platforms") or spec.supported_platforms),
        default_always_on=bool(
            contract.get("default_always_on", spec.default_always_on)
        ),
        palette_group=str(contract.get("palette_group") or spec.palette_group),
        configurable=bool(contract.get("configurable", spec.configurable)),
        hot_loadable=bool(contract.get("hot_loadable", spec.hot_loadable)),
    )


def register_view_type(spec: ViewTypeSpec, *, override: bool = False) -> None:
    """Register a ``ViewTypeSpec`` in the global registry."""
    global _REGISTRY_VERSION
    if not spec.type:
        raise ValueError("ViewTypeSpec.type required")
    if (
        spec.source == "plugin"
        and "/" not in spec.type
        and spec.type not in _LEGACY_UNNAMESPACED_PLUGIN_VIEW_TYPES
    ):
        raise ValueError(
            f"plugin view type '{spec.type}' must be namespaced as "
            "'pack-name/view-name' (see docs/content-profiles/UI_PACKS.md)"
        )
    if spec.type in _REGISTRY and not override:
        raise ValueError(f"view type '{spec.type}' already registered")
    _REGISTRY[spec.type] = spec
    _REGISTRY_VERSION += 1


def get(type_: str) -> Optional[ViewTypeSpec]:
    """Return the ``ViewTypeSpec`` for ``type_`` or ``None`` if unknown."""
    return _REGISTRY.get(type_)


def is_known(type_: str) -> bool:
    """Return True if ``type_`` is registered globally."""
    return type_ in _REGISTRY


def iter_specs() -> Iterable[ViewTypeSpec]:
    """Return all registered ``ViewTypeSpec`` instances."""
    return list(_REGISTRY.values())


def allowed_keys() -> List[str]:
    """Return the sorted list of registered view-type keys."""
    return sorted(_REGISTRY.keys())


def registry_version() -> int:
    """Return a monotonic counter used to invalidate compilation caches."""
    return _REGISTRY_VERSION


def resolve(
    type_: str,
    *,
    profile_composites: Optional[Dict[str, ViewTypeSpec]] = None,
) -> Optional[ViewTypeSpec]:
    """Resolve ``type_`` against profile-scoped composites then the global registry."""
    if profile_composites and type_ in profile_composites:
        return profile_composites[type_]
    return _REGISTRY.get(type_)


def primitive_for(spec: ViewTypeSpec) -> str:
    """Walk a composite's ``base`` chain and return the underlying primitive type."""
    seen: List[str] = []
    cur = spec
    while cur.base:
        if cur.type in seen:
            raise ValueError(f"composite cycle detected for view type '{cur.type}'")
        seen.append(cur.type)
        nxt = _REGISTRY.get(cur.base)
        if nxt is None:
            return cur.base
        cur = nxt
    return cur.type


def make_composite_spec(
    *,
    key: str,
    base: str,
    config: Optional[Dict[str, Any]] = None,
    label: str = "",
    description: str = "",
) -> ViewTypeSpec:
    """Build a profile-scoped composite ``ViewTypeSpec`` from ``key`` + ``base``."""
    return ViewTypeSpec(
        type=key,
        base=base,
        config_schema={},
        composite_config=dict(config or {}),
        source="composite",
        label=label or key,
        description=description,
    )


# ---------------------------------------------------------------------------
# Built-in primitive widget registrations
# ---------------------------------------------------------------------------

_BUILTIN_VIEW_TYPES: List[ViewTypeSpec] = [
    ViewTypeSpec(
        type="feed",
        label="Feed",
        description="Chronological list of entries; baseline always available.",
        default_always_on=True,
        palette_group="core",
    ),
    ViewTypeSpec(
        type="kanban",
        label="Kanban",
        description="Column board; columns from a select field or explicit kanban_columns config.",
        config_schema={
            "kanban_columns": {
                "type": "array",
                "description": "Optional explicit columns; otherwise derived from group_by.",
            },
            "group_by": {
                "type": "string",
                "description": "Field key whose values become columns.",
            },
        },
        palette_group="core",
    ),
    ViewTypeSpec(
        type="table",
        label="Table",
        description="Tabular grid with sortable, filterable columns.",
        config_schema={
            "columns": {
                "type": "array",
                "description": "Explicit column order/visibility; defaults to all entry-type fields.",
            },
        },
        palette_group="core",
    ),
    ViewTypeSpec(
        type="calendar",
        label="Calendar",
        description="Time-based view; entries placed by a date/datetime field.",
        config_schema={
            "calendar_mapping": {
                "type": "object",
                "description": "{ date_field, end_date_field? } — fields keyed for placement.",
            },
        },
        palette_group="core",
    ),
    ViewTypeSpec(
        type="gallery",
        label="Gallery",
        description="Card grid with image preview and configurable card layout.",
        palette_group="core",
    ),
    ViewTypeSpec(
        type="wiki",
        label="Pages",
        description=("Hierarchical pages with sidebar tree and markdown reader."),
        config_schema={
            "parent_field": {
                "type": "string",
                "description": "Relation field key whose value is the parent page entry id.",
            },
            "body_field": {
                "type": "string",
                "description": "Markdown body field (default: body).",
            },
            "title_field": {
                "type": "string",
                "description": "Page title field for nav labels (default: title).",
            },
            "sort_siblings": {
                "type": "object",
                "description": "{ field, direction } sort within each tree level.",
            },
            "default_page_id": {
                "type": "string",
                "description": "Optional entry id to select on initial load.",
            },
        },
        palette_group="core",
    ),
]


# ---------------------------------------------------------------------------
# Composable meta-widget registrations (Pillar 4)
# ---------------------------------------------------------------------------
#
# These are generic widgets driven entirely by declarative grouping/sorting/
# filtering/projection rules. Most "I want X view of Y data" agent requests
# resolve to a meta-widget config — no new code, no plugin install.

_META_WIDGET_TYPES: List[ViewTypeSpec] = [
    ViewTypeSpec(
        type="composable_list",
        label="Composable list",
        description=(
            "Generic declarative list. Config drives grouping, sort, filter, "
            "projection, and density."
        ),
        config_schema={
            "group_by": {"type": "string"},
            "sort": {"type": "array"},
            "filter": {"type": "object"},
            "projection": {"type": "array"},
            "density": {"type": "string", "enum": ["compact", "comfortable", "roomy"]},
        },
        palette_group="composable",
    ),
    ViewTypeSpec(
        type="composable_grid",
        label="Composable grid",
        description=(
            "Card grid driven by declarative grouping, color-coding, projection, "
            "and card layout."
        ),
        config_schema={
            "group_by": {"type": "string"},
            "color_by": {"type": "string"},
            "projection": {"type": "array"},
            "card_layout": {"type": "string"},
        },
        palette_group="composable",
    ),
    ViewTypeSpec(
        type="composable_board",
        label="Composable board",
        description=(
            "Kanban-style board with declarative grouping, swimlanes, color, and "
            "in-column sort."
        ),
        config_schema={
            "group_by": {"type": "string", "description": "Field key for columns."},
            "color_by": {"type": "string"},
            "swimlanes": {"type": "string"},
            "sort_within_column": {"type": "array"},
        },
        palette_group="composable",
    ),
    ViewTypeSpec(
        type="composable_timeline",
        label="Composable timeline",
        description=(
            "Time-axis view with declarative date fields, grouping, and color."
        ),
        config_schema={
            "date_field": {"type": "string"},
            "end_date_field": {"type": "string"},
            "group_by": {"type": "string"},
            "color_by": {"type": "string"},
        },
        palette_group="composable",
    ),
]

for _spec in (*_BUILTIN_VIEW_TYPES, *_META_WIDGET_TYPES):
    register_view_type(_with_contract_defaults(_spec))
