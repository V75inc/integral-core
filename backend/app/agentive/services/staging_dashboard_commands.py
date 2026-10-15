"""Atomic approved dashboard commands and current-authority receipt recovery."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from app.agentive.staging import StagedChange

DASHBOARD_COMMAND_KINDS = frozenset(
    {"create_dashboard", "update_dashboard", "delete_dashboard"}
)


async def apply_dashboard_command(
    *,
    user_id: str,
    payload: dict[str, Any],
    kind: str,
    execute: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Commit graph, event and receipt together under current workspace authority."""
    from app.agentive.services.staging_execution import (
        UnappliedStagedCommand,
        deliver_staging_command_events,
        staged_command_effect,
    )
    from app.services.membership_write_scope import membership_write_scope
    from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource

    app_id = str(payload["app_id"])
    location = await locate_resource(
        "app", app_id, allowed_app_states=MANAGEMENT_APP_STATES
    )
    if location is None:
        return {"error": True, "error_code": "not_found", "status_code": 404}
    try:
        async with membership_write_scope(
            location.workspace_id, principal_ids=(user_id,)
        ):
            current = await locate_resource(
                "app", app_id, allowed_app_states=MANAGEMENT_APP_STATES
            )
            if current != location:
                return {
                    "error": True,
                    "error_code": "scope_changed",
                    "status_code": 403,
                }
            async with staged_command_effect(
                user_id=user_id,
                workspace_id=location.workspace_id,
                kind=kind,
                targets=dict(payload),
            ) as complete:
                result = await execute()
                if result.get("error") or result.get("ok") is False:
                    raise UnappliedStagedCommand(result)
                if complete is not None:
                    await complete(
                        {
                            "app_id": app_id,
                            "dashboard_id": (
                                payload["dashboard_id"]
                                if kind == "delete_dashboard"
                                else result["id"]
                            ),
                            "deleted": kind == "delete_dashboard",
                        }
                    )
    except UnappliedStagedCommand as exc:
        return exc.result
    await deliver_staging_command_events()
    return result


async def readback(
    sc: StagedChange, workspace_id: str, evidence: dict[str, Any]
) -> dict[str, Any]:
    """Return the applied fact or a fresh authorized Dashboard, never an old write."""
    from app.agentive.services.staging_command_apply import _current_record_readback
    from app.agentive.services.staging_execution import execution_record_id
    from app.api.apps_dashboards import get_app_dashboard
    from app.api.errors import InsufficientPermissionsError, ResourceNotFoundError
    from app.services.agent_scope import current_scope_workspace_id
    from app.services.release_admission import release_admission
    from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource

    result: dict[str, Any] = {
        "ok": True,
        "applied": True,
        "result_access": "withheld",
        "message": "This approved dashboard change already applied. Current content is unavailable.",
    }
    if evidence.get("deleted"):
        result.update(
            deleted=True, message="This approved dashboard deletion already applied."
        )
    else:
        dashboard_id = evidence.get("dashboard_id", "")
        app_id = evidence.get("app_id", "")
        location = await locate_resource(
            "dashboard", dashboard_id, allowed_app_states=MANAGEMENT_APP_STATES
        )
        if (
            location is not None
            and location.workspace_id == workspace_id
            and location.app_id == app_id
        ):
            reset = current_scope_workspace_id.set(workspace_id)
            try:
                async with release_admission(sc.user_id, workspace_id):
                    observed = await _current_record_readback(
                        get_app_dashboard,
                        sc.user_id,
                        app_id=app_id,
                        dashboard_id=dashboard_id,
                    )
                    if observed is not None:
                        result = observed
                        result["message"] = (
                            "This approved dashboard change already applied. Current dashboard returned."
                        )
            except (InsufficientPermissionsError, ResourceNotFoundError):
                pass
            finally:
                current_scope_workspace_id.reset(reset)
    result["execution_receipt"] = {
        "id": execution_record_id(sc.token),
        "status": "applied",
        "replayed": True,
    }
    return result
