"""Read a package's prescribed home without provisioning mutable dashboard nodes."""

from __future__ import annotations

import asyncio
import copy
import time
from typing import Any, Dict, Optional

from app.models.nodes import App, ApplicationDefinition
from app.services.dashboard_service import resolve_widget_data
from app.services.permissions import can_view_app


def home_revision_token(app: Any) -> str:
    """Opaque edit token changes on local edits and active definition upgrades."""
    revision = int(getattr(app, "home_revision", 0) or 0)
    return f"{revision}:{getattr(app, 'active_definition_id', '') or ''}"


async def read_app_home(
    *,
    user_id: str,
    app_id: str,
    workspace_id: Optional[str],
    include_data: bool = True,
    preview_home: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Read the active owned declaration; resolve only governed, scoped queries."""
    if not await can_view_app(user_id, app_id):
        raise PermissionError("Access denied")
    app = await App.get(app_id)
    if app is None:
        raise ValueError("App not found")
    if not workspace_id or workspace_id != app.workspace_id:
        raise PermissionError("Home workspace is not active")
    home = getattr(app, "home_composition", None)
    source = "instance" if home else "package"
    definition_revision = None
    definition = None
    components = []
    definition_id = str(getattr(app, "active_definition_id", "") or "")
    if definition_id:
        definition = await ApplicationDefinition.get(definition_id)
        if (
            definition is not None
            and definition.app_id == app_id
            and definition.status == "active"
        ):
            definition_revision = definition.revision
            from app.services.home_native import native_component_catalog

            components = native_component_catalog()
        else:
            definition = None
    active_contract = (
        (definition.canonical_manifest.get("app") or {}) if definition else {}
    )
    bindings = {
        "queries": [
            {
                key: row[key]
                for key in ("key", "name", "input_schema", "output_schema")
                if key in row
            }
            for row in active_contract.get("queries", [])
        ],
        "operations": [
            {
                key: row[key]
                for key in ("key", "name", "kind", "input_schema")
                if key in row
            }
            for row in active_contract.get("operations", [])
        ],
    }
    metadata = {
        "home_revision": home_revision_token(app),
        "definition_revision": definition_revision,
        "source": "preview" if preview_home is not None else source,
        "components": components,
        "bindings": bindings,
        "preview": preview_home is not None,
        "live_data": include_data,
    }
    if preview_home is not None:
        from app.services.app_home_override import validate_home_composition

        home = await validate_home_composition(
            user_id=user_id, app_id=app_id, home=preview_home
        )
        # An explicit reset previews the package default without persisting it.
        if not home and definition is not None:
            home = (definition.canonical_manifest.get("app") or {}).get("home")
    if not home:
        if definition is None:
            return {"home": None, **metadata}
        home = (definition.canonical_manifest.get("app") or {}).get("home")
    if not isinstance(home, dict) or not home:
        return {"home": None, **metadata}
    result = copy.deepcopy(home)
    result.setdefault("actions", [])
    if result.get("renderer") in {"native", "extension"} or any(
        widget.get("type") in {"native_component", "extension_view"}
        for widget in result.get("widgets", [])
    ):
        from app.services.home_native import validate_native_home

        if getattr(app, "lifecycle_state", "active") != "active":
            return {"home": None, **metadata, "error": "app_not_active"}
        try:
            validate_native_home(
                result, definition.canonical_manifest if definition else {}
            )
        except ValueError:
            return {"home": None, **metadata, "error": "home_extension_unavailable"}
    if not include_data:
        return {"home": result, **metadata}
    if not workspace_id or workspace_id != app.workspace_id:
        raise PermissionError("Home workspace is not active")
    deadline = time.monotonic() + 5
    for widget in result.get("widgets", []):
        if widget.get("type") in {"native_component", "extension_view"}:
            continue
        if app.lifecycle_state != "active":
            widget["data"] = {"error": "app_not_active"}
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            widget["data"] = {"error": "home_read_budget_exceeded"}
            continue
        try:
            widget["data"] = await asyncio.wait_for(
                resolve_widget_data(
                    user_id=user_id,
                    app_id=app_id,
                    workspace_id=workspace_id,
                    widget=widget,
                ),
                timeout=remaining,
            )
        except Exception:
            widget["data"] = {"error": "home_query_unavailable"}
    return {"home": result, **metadata}


async def preview_home_for_agent(
    *, user_id: str, app_id: str, home: Dict[str, Any], include_data: bool = False
) -> Dict[str, Any]:
    """Use the conversation-bound workspace; tool inputs cannot widen scope."""
    from app.services.agent_scope import current_scope_workspace_id

    return await read_app_home(
        user_id=user_id,
        app_id=app_id,
        workspace_id=current_scope_workspace_id.get(),
        include_data=include_data,
        preview_home=home,
    )
