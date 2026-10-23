"""Workspace-owned App Home composition through ordinary App update approval."""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import ValidationError

from app.schemas.app_home import AppHomeComposition
from app.schemas.dashboards import DashboardWidgetSpec
from app.services.dashboard_service import validate_dashboard_field_bindings
from app.services.dashboard_widget_validation import validate_widget_specs
from app.services.home_native import validate_native_home


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
        issues = (
            ["widgets: required field"]
            if home.get("renderer", "widgets") == "widgets" and "widgets" not in home
            else []
        )
        for error in exc.errors(include_input=False, include_context=True)[:8]:
            path = ".".join(
                (
                    str(segment)
                    if isinstance(segment, int) or segment in known_fields
                    else "<extra field>"
                )
                for segment in error["loc"]
                if segment not in {"HomeComponentWidget", "HomeNativeWidget"}
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
            canonical = str((error.get("ctx") or {}).get("error", ""))
            if canonical in {
                "Home widgets must not overlap",
                "Home widget IDs must be unique and bounded",
                "Home widget placement is out of bounds",
                "Home title is required",
            }:
                reason = canonical
            issues.append(f"{path or 'home'}: {reason}")
        raise ValueError("Invalid Home arguments: " + "; ".join(issues)) from exc
    native = [
        widget
        for widget in normalized["widgets"]
        if widget["type"] != "native_component"
    ]
    errors = validate_widget_specs(native)
    if errors:
        raise ValueError("Invalid Home widgets: " + "; ".join(errors))
    if native:
        await validate_dashboard_field_bindings(
            user_id=user_id, app_id=app_id, widgets=native
        )
    if normalized["renderer"] == "native" or len(native) != len(normalized["widgets"]):
        from app.models.nodes import App
        from app.services.app_extension_views import (
            _compiled_app_manifest,
            _ensure_app_access,
        )

        app = await App.get(app_id)
        if app is None:
            raise ValueError("App not found")
        await _ensure_app_access(app, user_id, app.workspace_id)
        if not getattr(app, "active_definition_id", ""):
            raise ValueError("Custom Home requires an active App definition")
        canonical = await _compiled_app_manifest(app)
        validate_native_home(normalized, canonical)
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


async def prepare_suggested_home(
    *, user_id: str, app_id: str, widgets: List[Dict[str, Any]]
) -> Dict[str, Any] | None:
    """Offer only a bounded, valid starter; no write, fallback scan or consent.

    Omit unsupported suggestions rather than handing the model an invalid
    composition. Repack a single column to keep layout deterministic even when
    optional candidate widgets are dropped. Titles remain the App's own labels.
    """
    selected: List[Dict[str, Any]] = []
    y = 0
    seen: set[str] = set()
    fields = set(DashboardWidgetSpec.model_fields)
    for widget in widgets:
        candidate = {key: value for key, value in widget.items() if key in fields}
        grid = dict(candidate.get("grid") or {})
        height = grid.get("h", 2)
        if not isinstance(height, int) or not 1 <= height <= 12 or y > 100:
            continue
        candidate["grid"] = {"x": 0, "y": y, "w": 12, "h": height}
        proposed = {"title": "Home", "widgets": [candidate]}
        try:
            normalized = await validate_home_composition(
                user_id=user_id, app_id=app_id, home=proposed
            )
        except (ValueError, PermissionError):
            continue
        normalized_widget = normalized["widgets"][0]
        if normalized_widget["id"] in seen:
            continue
        seen.add(normalized_widget["id"])
        selected.append(normalized_widget)
        y += height
        if len(selected) == 12:
            break
    return (
        AppHomeComposition(title="Home", widgets=selected).model_dump(
            mode="json", exclude_none=True
        )
        if selected
        else None
    )
