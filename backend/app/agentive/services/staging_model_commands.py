"""Atomic approved model edits and current-authority recovery projections."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from app.agentive.staging import StagedChange

MODEL_COMMAND_KINDS = frozenset(
    f"modify_operational_model.{action}"
    for action in (
        "add_entry_type",
        "remove_entry_type",
        "add_view",
        "update_view",
        "remove_view",
        "add_tag",
        "remove_tag",
    )
)


async def apply_model_command(
    *,
    user_id: str,
    payload: dict[str, Any],
    kind: str,
    execute: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Commit the selected model edit, its event and private receipt together."""
    from app.agentive.services.staging_execution import (
        UnappliedStagedCommand,
        deliver_staging_command_events,
        staged_command_effect,
    )
    from app.services.attached_model_scope import read_attached_model
    from app.services.membership_write_scope import membership_write_scope
    from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource

    if not payload.get("track_id") and not payload.get("app_id"):
        return {"error": "missing_argument", "detail": "track_id or app_id is required"}
    if payload.get("track_id") and payload.get("app_id"):
        return {
            "error": "invalid_argument",
            "detail": "Provide track_id OR app_id, not both",
        }
    owner_kind = "track" if payload.get("track_id") else "app"
    owner_id = str(payload[f"{owner_kind}_id"])
    location = await locate_resource(
        owner_kind, owner_id, allowed_app_states=MANAGEMENT_APP_STATES
    )
    if location is None:
        return {"error": "not_found", "detail": "Target not found"}
    try:
        async with membership_write_scope(
            location.workspace_id, principal_ids=(user_id,)
        ):
            current = await locate_resource(
                owner_kind, owner_id, allowed_app_states=MANAGEMENT_APP_STATES
            )
            attached = await read_attached_model(owner_kind, owner_id)
            if current != location or attached is None:
                return {
                    "error": "not_found",
                    "detail": "Current attached model is unavailable",
                }
            action = str(payload["action"])
            targets = dict(payload)
            if "app_id" in targets:
                targets["app_id"] = targets["app_id"] or None
            async with staged_command_effect(
                user_id=user_id,
                workspace_id=location.workspace_id,
                kind=kind,
                targets=targets,
            ) as complete:
                result = await execute()
                if result.get("error") or result.get("ok") is False:
                    raise UnappliedStagedCommand(result)
                if complete is not None:
                    child_kind = (
                        action.removeprefix("add_")
                        .removeprefix("remove_")
                        .removeprefix("update_")
                    )
                    await complete(
                        {
                            "owner_kind": owner_kind,
                            "owner_id": owner_id,
                            "model_id": attached[1].id,
                            "action": action,
                            f"{child_kind}_id": result[f"{child_kind}_id"],
                            "deleted": action.startswith("remove_"),
                        }
                    )
    except UnappliedStagedCommand as exc:
        return exc.result
    await deliver_staging_command_events()
    return result


async def readback(
    sc: StagedChange, workspace_id: str, evidence: dict[str, Any]
) -> dict[str, Any]:
    """Project current authorized configuration without replaying the edit."""
    from app.agentive.services.staging_execution import execution_record_id
    from app.api.errors import InsufficientPermissionsError, ResourceNotFoundError
    from app.api.utils import export_node
    from app.schemas.policy import Resource, Subject
    from app.services.agent_scope import current_scope_workspace_id
    from app.services.attached_model_scope import read_attached_model
    from app.services.policy_engine import evaluate
    from app.services.release_admission import release_admission
    from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource

    result: dict[str, Any] = {
        "ok": True,
        "applied": True,
        "result_access": "withheld",
        "message": "This approved model edit already applied. Current configuration is unavailable.",
    }
    if evidence.get("deleted"):
        result["deleted"] = True
    reset = current_scope_workspace_id.set(workspace_id)
    try:
        async with release_admission(sc.user_id, workspace_id):
            owner_kind, owner_id = evidence["owner_kind"], evidence["owner_id"]
            location = await locate_resource(
                owner_kind, owner_id, allowed_app_states=MANAGEMENT_APP_STATES
            )
            if location is not None and location.workspace_id == workspace_id:
                attached = await read_attached_model(owner_kind, owner_id)
                if attached is not None and attached[1].id == evidence["model_id"]:
                    decision = await evaluate(
                        subject=Subject(kind="human", id=sc.user_id),
                        action="operational_model.read",
                        resource=Resource(
                            kind="operational_model",
                            id=attached[1].id,
                            scope=f"{owner_kind}:{owner_id}",
                        ),
                    )
                    if decision.allowed:
                        result.update(
                            operational_model=await export_node(attached[1]),
                            result_access="current",
                            message="This approved model edit already applied. Current configuration returned.",
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
