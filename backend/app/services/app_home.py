"""Read a package's prescribed home without provisioning mutable dashboard nodes."""

from __future__ import annotations

import asyncio
import copy
import time
from typing import Any, Dict, Optional

from app.models.nodes import App, ApplicationDefinition
from app.services.dashboard_service import resolve_widget_data
from app.services.permissions import can_view_app


async def read_app_home(
    *, user_id: str, app_id: str, workspace_id: Optional[str], include_data: bool = True
) -> Dict[str, Any]:
    """Read the active owned declaration; resolve only governed, scoped queries."""
    if not await can_view_app(user_id, app_id):
        raise PermissionError("Access denied")
    app = await App.get(app_id)
    if app is None:
        raise ValueError("App not found")
    definition_id = str(getattr(app, "active_definition_id", "") or "")
    definition = (
        await ApplicationDefinition.get(definition_id) if definition_id else None
    )
    if (
        definition is None
        or definition.app_id != app_id
        or definition.status != "active"
    ):
        return {"home": None}
    home = (definition.canonical_manifest.get("app") or {}).get("home")
    if not isinstance(home, dict) or not home:
        return {"home": None}
    result = copy.deepcopy(home)
    if not include_data:
        return {"home": result, "definition_revision": definition.revision}
    if not workspace_id or workspace_id != app.workspace_id:
        raise PermissionError("Home workspace is not active")
    deadline = time.monotonic() + 5
    for widget in result.get("widgets", []):
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
    return {"home": result, "definition_revision": definition.revision}
