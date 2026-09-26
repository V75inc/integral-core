"""Design coverage (W1.4): can the live substrate build each blueprint item?

Every field, view, widget and operation is classified as ``native`` (a
built-in palette type), ``installed_extension`` (a registered composite or
plugin type), ``requires_trusted_package`` (needs code only a trusted package
provides) or ``unsupported`` (the build would fail or silently drop it).
Checks defer to the code that builds: fields go through the compile step's own
field validator, view config through the persisted view contract and the
build's binding rules, widgets through the dashboard registry.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.services import operational_model_field_types as field_types
from app.services.design_blueprint import validate_blueprint
from app.services.operational_model_compile import (
    BUILTIN_VIEW_CONFIG_KEYS,
    _normalize_field_spec,
)
from app.views import dashboard_widget_types as widget_types
from app.views import operational_model_view_types as view_types

NATIVE = "native"
INSTALLED_EXTENSION = "installed_extension"
REQUIRES_TRUSTED_PACKAGE = "requires_trusted_package"
UNSUPPORTED = "unsupported"

# Shorthands the scaffold build folds into the persisted keys.
_BUILD_VIEW_CONFIG_ALIASES = frozenset(
    {"date_field", "filter", "hierarchy_field", "parent", "track_hint", "name", "type"}
)
_SYSTEM_COLUMNS = frozenset(
    {"title", "name", "status", "tags", "created_at", "updated_at", "author"}
)


def _field_key(ref: Any) -> str:
    return str(ref or "").strip().removeprefix("custom_fields.")


def _view_binding_problem(
    kind: str, config: Dict[str, Any], fields: Dict[str, str]
) -> str:
    """Why the build cannot bind a ``kind`` view to its track's fields, or ``""``."""
    if kind == "kanban":
        group = _field_key(config.get("group_by"))
        if group and group != "status" and fields.get(group) != "select":
            return f"a board groups by a select field; {group!r} is not one"
        if "select" not in fields.values():
            return "a board groups by a select field and this track has none"
    elif kind == "calendar":
        mapping = config.get("calendar_mapping") or {}
        bound = _field_key(config.get("date_field") or mapping.get("dateField"))
        if bound and fields.get(bound) not in ("date", "datetime"):
            return f"a calendar places entries by a date field; {bound!r} is not one"
        if not bound and "date" not in fields.values():
            return "a calendar places entries by a date field and this track has none"
    elif kind == "wiki":
        parent = _field_key(
            config.get("parent_field")
            or config.get("hierarchy_field")
            or config.get("parent")
        )
        if parent and fields.get(parent) != "relation":
            return f"a wiki nests pages by a relation field; {parent!r} is not one"
        if "relation" not in fields.values():
            return "a wiki nests pages by a relation field and this track has none"
    elif kind == "table":
        for column in config.get("columns") or []:
            ref = column.get("field") if isinstance(column, dict) else column
            key = _field_key(ref)
            if key and key not in fields and key not in _SYSTEM_COLUMNS:
                return f"table column {key!r} is not a field on this track"
    return ""


async def check_design_coverage(user_id: str, blueprint: Any) -> Dict[str, Any]:
    """Classify every buildable item of ``blueprint`` against the live palette.

    ``user_id`` is the dispatch identity; coverage reads only global registries.
    """
    canonical, error = validate_blueprint(blueprint)
    if error:
        return {"error": "invalid_blueprint", "message": error}
    items: List[Dict[str, Any]] = []

    def add(
        item_id: str,
        requirement: str,
        classification: str,
        detail: str = "",
        name: str = "",
    ):
        row = {"id": item_id, "requirement": requirement, "class": classification}
        if detail:
            row["detail"] = detail
        if name:
            row["name"] = name
        items.append(row)

    buildable_types = []
    for key in sorted(field_types.allowed_keys()):
        try:
            _normalize_field_spec({"key": "probe", "type": key})
            buildable_types.append(key)
        except Exception:  # noqa: BLE001 — registered but not buildable
            pass
    field_palette = ", ".join(buildable_types)
    track_fields: Dict[str, Dict[str, str]] = {}
    for track in [*canonical["tracks"], *canonical["track_templates"]]:
        fields = track_fields.setdefault(track["id"], {})
        for entry_type in track["entry_types"]:
            for field in entry_type["fields"]:
                ftype = field["type"].strip().lower()
                fields[field["key"]] = ftype
                label = f"{track['name']} field {field['name']!r} ({ftype})"
                spec = field_types.get(ftype)
                try:
                    _normalize_field_spec(
                        {"key": field["key"], "name": field["name"], "type": ftype}
                    )
                except (
                    Exception
                ) as exc:  # noqa: BLE001 — compile's refusal is the verdict
                    detail = getattr(exc, "message", None) or str(exc)
                    if spec is None:
                        detail += f"; available field types: {field_palette}"
                    add(field["id"], label, UNSUPPORTED, detail)
                    continue
                if ftype in ("select", "multi_select") and not field["options"]:
                    add(field["id"], label, UNSUPPORTED, "list the choices as options")
                elif spec is not None and spec.source != "builtin":
                    add(field["id"], label, INSTALLED_EXTENSION, f"{spec.source} type")
                else:
                    add(field["id"], label, NATIVE)

    track_names = {t["id"]: t["name"] for t in canonical["tracks"]}
    view_palette = ", ".join(sorted(view_types.allowed_keys()))
    for view in canonical["views"]:
        kind = view["type"].strip().lower()
        label = f"{track_names[view['track']]} view {view['name']!r} ({kind})"
        spec = view_types.get(kind)
        if spec is None:
            add(
                view["id"],
                label,
                UNSUPPORTED,
                f"unknown view type; available view types: {view_palette}",
            )
            continue
        if kind == "extension_view":
            add(
                view["id"],
                label,
                REQUIRES_TRUSTED_PACKAGE,
                "extension views ship inside a trusted App package",
                name=view["name"],
            )
            continue
        if spec.source != "builtin":
            add(view["id"], label, INSTALLED_EXTENSION, f"{spec.source} type")
            continue
        config = view.get("config") or {}
        dropped = sorted(
            set(config) - BUILTIN_VIEW_CONFIG_KEYS - _BUILD_VIEW_CONFIG_ALIASES
        )
        problem = _view_binding_problem(kind, config, track_fields[view["track"]])
        if dropped:
            problem = (
                f"config {', '.join(dropped)} is not part of the {kind} view contract"
            )
        add(view["id"], label, UNSUPPORTED if problem else NATIVE, problem)

    widget_palette = ", ".join(sorted(widget_types.allowed_keys()))
    for widget in (canonical.get("dashboard") or {}).get("widgets") or []:
        raw = widget["type"].strip().casefold()
        kind = widget_types.TYPE_ALIASES.get(raw, raw)
        label = f"dashboard widget {widget['title']!r} ({kind})"
        spec = widget_types.get_spec(kind)
        if spec is None:
            add(
                widget["id"],
                label,
                UNSUPPORTED,
                f"unknown widget type; available widget types: {widget_palette}",
            )
        elif spec.source != "builtin":
            add(widget["id"], label, INSTALLED_EXTENSION, f"{spec.source} type")
        else:
            add(widget["id"], label, NATIVE)

    for operation in canonical["operations"]:
        add(
            operation["id"],
            f"operation {operation['name']!r}",
            REQUIRES_TRUSTED_PACKAGE,
            "runs custom code, which only a trusted App package can provide",
            name=operation["name"],
        )

    classes = {row["class"] for row in items}
    if UNSUPPORTED in classes:
        status = "unsupported"
    elif REQUIRES_TRUSTED_PACKAGE in classes:
        status = "needs_trusted_package"
    else:
        status = "buildable"
    return {
        "status": status,
        "unsupported": [r for r in items if r["class"] == UNSUPPORTED],
        "requires_trusted_package": [
            r for r in items if r["class"] == REQUIRES_TRUSTED_PACKAGE
        ],
        "items": items,
        "note": (
            "Skills, routines, seeds, relations, track templates and access "
            "are native to every design."
        ),
    }
