"""Per-workspace app operation registration table."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# workspace_id -> app_id -> operation_key -> spec
_OPERATIONS: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {}


def register_app_operations(
    workspace_id: str,
    app_id: str,
    bundle_slug: str,
    operations: List[Dict[str, Any]],
) -> None:
    """Register manifest operations for a workspace app instance."""
    if not operations:
        return
    ws = _OPERATIONS.setdefault(workspace_id, {})
    bucket = ws.setdefault(app_id, {})
    for op in operations:
        key = str(op.get("key") or "").strip()
        if not key:
            continue
        bucket[key] = {**op, "_bundle_slug": bundle_slug, "_app_id": app_id}
    logger.info(
        "registered %d operations for app %s workspace %s",
        len(operations),
        app_id,
        workspace_id,
    )


def unregister_app_operations(workspace_id: str, app_id: str) -> None:
    """Drop all operations for an app instance."""
    ws = _OPERATIONS.get(workspace_id)
    if not ws:
        return
    ws.pop(app_id, None)
    if not ws:
        _OPERATIONS.pop(workspace_id, None)


def get_app_operation(
    workspace_id: str, app_id: str, operation_key: str
) -> Dict[str, Any] | None:
    """Look up a registered operation spec."""
    return (_OPERATIONS.get(workspace_id) or {}).get(app_id, {}).get(operation_key)


def list_registered_operations(
    workspace_id: str, app_id: str
) -> Dict[str, Dict[str, Any]]:
    """Return all operations registered for an app instance."""
    return dict((_OPERATIONS.get(workspace_id) or {}).get(app_id) or {})


def list_workspace_operations(
    workspace_id: str,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Return app_id → operations for one workspace (cache enumeration)."""
    return {
        app_id: dict(ops)
        for app_id, ops in (_OPERATIONS.get(workspace_id) or {}).items()
    }


def clear_workspace_operations(workspace_id: str) -> None:
    """Clear the operation table for a workspace (tests)."""
    _OPERATIONS.pop(workspace_id, None)
