"""Shared admitted saved View deletion for HTTP and approved agent commands."""

from typing import Any, Awaitable, Callable

from app.agentive.staging import StagingError


async def delete_saved_view(
    user_id: str,
    payload: dict[str, Any],
    execute: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Commit deletion, manifest synchronization, event and receipt together."""
    from app.agentive.services.staging_execution import (
        UnappliedStagedCommand,
        deliver_staging_command_events,
        staged_command_effect,
    )
    from app.api.errors import ResourceNotFoundError
    from app.services.membership_write_scope import membership_write_scope
    from app.services.resource_location import MANAGEMENT_APP_STATES
    from app.services.view_scope import read_scoped_view

    view_id = str(payload["view_id"])
    located = await read_scoped_view(view_id, allowed_app_states=MANAGEMENT_APP_STATES)
    if located is None:
        raise ResourceNotFoundError(message="View not found")
    _, location = located
    try:
        async with membership_write_scope(
            location.workspace_id, principal_ids=(user_id,)
        ):
            current = await read_scoped_view(
                view_id, allowed_app_states=MANAGEMENT_APP_STATES
            )
            if current is None or current[1] != location:
                raise ResourceNotFoundError(message="Current View scope is unavailable")
            async with staged_command_effect(
                user_id=user_id,
                workspace_id=location.workspace_id,
                kind="delete_view",
                targets=dict(payload),
            ) as complete:
                result = await execute()
                if result.get("error") or result.get("ok") is False:
                    raise UnappliedStagedCommand(result)
                if result.get("deleted_view_id") != view_id:
                    raise StagingError(
                        "execution_result_invalid",
                        "View deletion returned an unexpected identity",
                    )
                if complete is not None:
                    await complete(
                        {
                            "view_id": view_id,
                            "track_id": location.track_id,
                            "deleted": True,
                        }
                    )
    except UnappliedStagedCommand as exc:
        return exc.result
    await deliver_staging_command_events()
    return result
