"""Rollback engine for consumed agent staging tokens.

Inverts ChangeEvents tagged with ``details.staging_token`` by restoring
``before`` snapshots (updates) or deleting created resources (creates).
MVP scope: entry + view mutations only; deletes and profile publishes
are rejected with a clear reason.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from app.schemas.provenance import ActorKind
from app.services.change_event import emit_change_event
from app.services.change_event_logger import (
    ChangeEventEnvelope,
    envelope_from_dblog,
    get_change_event_logger,
)
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

# Original actions the MVP rollback engine can invert.
_SUPPORTED_ACTIONS: Set[str] = {
    "entry.create",
    "entry.update",
    "view.create",
    "view.update",
    "dashboard.create",
    "dashboard.update",
}

# Actions that are never undoable in MVP (even when tagged with a token).
_UNSUPPORTED_ACTIONS: Set[str] = {
    "entry.delete",
    "track.delete",
    "view.delete",
    "dashboard.delete",
    "content_profile.publish",
    "anchor.cascade",
}


class RollbackError(Exception):
    """Rollback could not be completed."""

    def __init__(self, code: str, message: str) -> None:  # noqa: B042
        super().__init__(message)
        self.code = code
        self.message = message


def _envelope_from_row(row: Any) -> ChangeEventEnvelope:
    return envelope_from_dblog(row)


def _snapshot_missing(envelope: ChangeEventEnvelope) -> bool:
    action = envelope.action
    if action.endswith(".create"):
        return envelope.after is None
    if action.endswith(".update"):
        return envelope.before is None or envelope.after is None
    return True


def _conflict_fields(
    current: Dict[str, Any],
    expected_after: Dict[str, Any],
) -> List[str]:
    """Return field names that differ between current state and the agent write."""
    keys = (
        "title",
        "body",
        "name",
        "type",
        "view_type",
        "config",
        "custom_fields",
        "tags",
        "status",
        "type_id",
        "entry_type_keys",
        "default_entry_type_key",
        "is_default",
        "hidden",
        "layout",
        "widgets",
    )
    conflicts: List[str] = []
    for key in keys:
        if key not in expected_after:
            continue
        if current.get(key) != expected_after.get(key):
            conflicts.append(key)
    return conflicts


async def _load_current_export(
    resource_type: str, resource_id: str
) -> Optional[Dict[str, Any]]:
    from app.api.utils import export_node

    model_map = {
        "Entry": "app.models.nodes.Entry",
        "View": "app.models.nodes.View",
        "Dashboard": "app.models.nodes.Dashboard",
    }
    import_path = model_map.get(resource_type)
    if not import_path:
        return None
    module_name, class_name = import_path.rsplit(".", 1)
    import importlib

    mod = importlib.import_module(module_name)
    cls = getattr(mod, class_name)
    node = await cls.get(resource_id)
    if node is None:
        return None
    return await export_node(node)


async def _invert_event(
    *,
    envelope: ChangeEventEnvelope,
    user_id: str,
    force: bool,
) -> Dict[str, Any]:
    from app.agentive.staging_executors import _call_endpoint, _call_endpoint_with_json

    action = envelope.action
    resource_id = envelope.resource_id

    if action == "entry.create":
        from app.api.entries import delete_entry

        result = await _call_endpoint(delete_entry, user_id, entry_id=resource_id)
        if isinstance(result, dict) and result.get("error"):
            raise RollbackError(
                "delete_failed",
                str(result.get("message") or "Failed to delete created entry"),
            )
        return {"action": action, "resource_id": resource_id, "method": "delete"}

    if action == "entry.update":
        before = envelope.before or {}
        current = await _load_current_export("Entry", resource_id)
        if current is None:
            raise RollbackError(
                "resource_gone",
                f"Entry {resource_id!r} no longer exists",
            )
        conflicts = _conflict_fields(current, envelope.after or {})
        if conflicts and not force:
            raise RollbackError(
                "conflict",
                f"Entry was edited after the agent change ({', '.join(conflicts)})",
            )
        from app.api.entries import update_entry

        kwargs: Dict[str, Any] = {"entry_id": resource_id}
        if "title" in before:
            kwargs["title"] = before.get("title")
        if "body" in before:
            kwargs["body"] = before.get("body")
        if "custom_fields" in before:
            kwargs["custom_fields"] = before.get("custom_fields")
        if "tags" in before:
            kwargs["tags"] = before.get("tags")
        if "status" in before:
            kwargs["status"] = before.get("status")
        if "type_id" in before:
            kwargs["type_id"] = before.get("type_id")
        result = await _call_endpoint(update_entry, user_id, **kwargs)
        if isinstance(result, dict) and result.get("error"):
            raise RollbackError(
                "restore_failed",
                str(result.get("message") or "Failed to restore entry"),
            )
        return {
            "action": action,
            "resource_id": resource_id,
            "method": "restore",
            "conflicts": conflicts if conflicts else None,
        }

    if action == "view.create":
        from app.api.views import delete_view

        result = await _call_endpoint(delete_view, user_id, view_id=resource_id)
        if isinstance(result, dict) and result.get("error"):
            raise RollbackError(
                "delete_failed",
                str(result.get("message") or "Failed to delete created view"),
            )
        return {"action": action, "resource_id": resource_id, "method": "delete"}

    if action == "view.update":
        before = envelope.before or {}
        current = await _load_current_export("View", resource_id)
        if current is None:
            raise RollbackError(
                "resource_gone",
                f"View {resource_id!r} no longer exists",
            )
        conflicts = _conflict_fields(current, envelope.after or {})
        if conflicts and not force:
            raise RollbackError(
                "conflict",
                f"View was edited after the agent change ({', '.join(conflicts)})",
            )
        from app.api.views import update_view

        kwargs = {"view_id": resource_id}
        if "name" in before:
            kwargs["name"] = before.get("name")
        if "type" in before:
            kwargs["type"] = before.get("type")
        if "view_type" in before:
            kwargs["view_type"] = before.get("view_type")
        if "config" in before:
            kwargs["config"] = before.get("config")
        if "is_default" in before:
            kwargs["is_default"] = before.get("is_default")
        if "hidden" in before:
            kwargs["hidden"] = before.get("hidden")
        if "entry_type_keys" in before:
            kwargs["entry_type_keys"] = before.get("entry_type_keys")
        if "default_entry_type_key" in before:
            kwargs["default_entry_type_key"] = before.get("default_entry_type_key")
        result = await _call_endpoint(update_view, user_id, **kwargs)
        if isinstance(result, dict) and result.get("error"):
            raise RollbackError(
                "restore_failed",
                str(result.get("message") or "Failed to restore view"),
            )
        return {
            "action": action,
            "resource_id": resource_id,
            "method": "restore",
            "conflicts": conflicts if conflicts else None,
        }

    if action == "dashboard.create":
        from app.api.apps_dashboards import delete_app_dashboard

        app_id = (envelope.after or {}).get("app_id") or ""
        if not app_id:
            scope = envelope.scope or ""
            if scope.startswith("app:"):
                app_id = scope.split(":", 1)[1]
        result = await _call_endpoint(
            delete_app_dashboard,
            user_id,
            app_id=app_id,
            dashboard_id=resource_id,
        )
        if isinstance(result, dict) and result.get("error"):
            raise RollbackError(
                "delete_failed",
                str(result.get("message") or "Failed to delete created dashboard"),
            )
        return {"action": action, "resource_id": resource_id, "method": "delete"}

    if action == "dashboard.update":
        before = envelope.before or {}
        current = await _load_current_export("Dashboard", resource_id)
        if current is None:
            raise RollbackError(
                "resource_gone",
                f"Dashboard {resource_id!r} no longer exists",
            )
        conflicts = _conflict_fields(current, envelope.after or {})
        if conflicts and not force:
            raise RollbackError(
                "conflict",
                f"Dashboard was edited after the agent change ({', '.join(conflicts)})",
            )
        from app.api.apps_dashboards import patch_app_dashboard

        app_id = (envelope.after or {}).get("app_id") or (before or {}).get("app_id")
        if not app_id:
            scope = envelope.scope or ""
            if scope.startswith("app:"):
                app_id = scope.split(":", 1)[1]
        if not app_id:
            raise RollbackError(
                "restore_failed",
                "Dashboard app_id missing from change event",
            )
        body: Dict[str, Any] = {}
        if "name" in before:
            body["name"] = before.get("name")
        if "layout" in before:
            body["layout"] = before.get("layout")
        if "widgets" in before:
            body["widgets"] = before.get("widgets")
        if "is_default" in before:
            body["is_default"] = before.get("is_default")
        result = await _call_endpoint_with_json(
            patch_app_dashboard,
            user_id,
            body,
            app_id=app_id,
            dashboard_id=resource_id,
        )
        if isinstance(result, dict) and result.get("error"):
            raise RollbackError(
                "restore_failed",
                str(result.get("message") or "Failed to restore dashboard"),
            )
        return {
            "action": action,
            "resource_id": resource_id,
            "method": "restore",
            "conflicts": conflicts if conflicts else None,
        }

    raise RollbackError("unsupported_action", f"Cannot rollback action {action!r}")


async def assess_rollback(
    *,
    staging_token: str,
    user_id: str,
) -> Dict[str, Any]:
    """Return whether a consumed staging token can be rolled back."""
    rows = await get_change_event_logger().find_by_staging_token(staging_token)
    if not rows:
        return {
            "available": False,
            "reason": "no_events",
            "message": "No recorded changes for this approval",
        }

    envelopes = [_envelope_from_row(r) for r in rows]
    rolled = [
        e
        for e in envelopes
        if (e.details or {}).get("rolled_back_at")
        or (e.details or {}).get("rolled_back")
    ]
    if rolled:
        return {
            "available": False,
            "reason": "already_rolled_back",
            "message": "This change was already undone",
            "rolled_back_at": (rolled[0].details or {}).get("rolled_back_at"),
        }

    reversible: List[str] = []
    blocked: List[Dict[str, str]] = []
    for env in envelopes:
        if env.action in _UNSUPPORTED_ACTIONS:
            blocked.append(
                {
                    "action": env.action,
                    "reason": "unsupported_action",
                }
            )
            continue
        if env.action not in _SUPPORTED_ACTIONS:
            blocked.append(
                {
                    "action": env.action,
                    "reason": "unsupported_action",
                }
            )
            continue
        if _snapshot_missing(env):
            blocked.append(
                {
                    "action": env.action,
                    "reason": "snapshot_expired",
                }
            )
            continue
        reversible.append(env.action)

    if not reversible:
        reason = blocked[0]["reason"] if blocked else "unsupported_action"
        return {
            "available": False,
            "reason": reason,
            "message": "This change cannot be undone",
            "blocked": blocked,
        }

    if blocked:
        return {
            "available": False,
            "reason": "partially_supported",
            "message": "This change includes operations that cannot be undone",
            "blocked": blocked,
            "reversible": reversible,
        }

    return {
        "available": True,
        "event_count": len(reversible),
        "actions": reversible,
    }


async def rollback_staged_change(
    *,
    staging_token: str,
    user_id: str,
    force: bool = False,
    actor_kind: ActorKind = "human",
) -> Dict[str, Any]:
    """Invert all ChangeEvents for ``staging_token``, newest first."""
    assessment = await assess_rollback(staging_token=staging_token, user_id=user_id)
    if not assessment.get("available"):
        raise RollbackError(
            assessment.get("reason", "unavailable"),
            assessment.get("message", "Rollback is not available"),
        )

    rows = await get_change_event_logger().find_by_staging_token(staging_token)
    envelopes = [_envelope_from_row(r) for r in rows]
    to_invert = [
        e
        for e in envelopes
        if e.action in _SUPPORTED_ACTIONS
        and not (e.details or {}).get("rolled_back_at")
    ]
    to_invert.sort(key=lambda e: (e.ts, e.id), reverse=True)

    inverted: List[Dict[str, Any]] = []
    rollback_ids: List[str] = []
    ce_logger = get_change_event_logger()

    for envelope in to_invert:
        if _snapshot_missing(envelope):
            raise RollbackError(
                "snapshot_expired",
                f"Snapshot for {envelope.action} is no longer available",
            )
        result = await _invert_event(envelope=envelope, user_id=user_id, force=force)
        inverted.append(result)
        await ce_logger.patch_event_details(
            envelope.id,
            {
                "rolled_back": True,
                "rolled_back_at": utc_now_iso(),
                "rolled_back_by": user_id,
            },
        )
        rb = await emit_change_event(
            actor_kind=actor_kind,
            actor_id=user_id,
            action="staging.rollback",
            resource_type=envelope.resource_type,
            resource_id=envelope.resource_id,
            before=envelope.after,
            after=envelope.before,
            scope=envelope.scope,
            details={
                "staging_token": staging_token,
                "reverted_event_id": envelope.id,
                "reverted_action": envelope.action,
                "invert_method": result.get("method"),
            },
        )
        if rb.id:
            rollback_ids.append(rb.id)

    return {
        "ok": True,
        "staging_token": staging_token,
        "inverted": inverted,
        "rollback_event_ids": rollback_ids,
        "rolled_back_at": utc_now_iso(),
    }
