"""RoutineTask management REST surface.

Exposes list / get / patch / cancel / delete for the caller's own routines so
the Background Tasks UI and Inbox can manage work previously reachable only
via MCP tools (``integral_list_routines`` / ``integral_update_routine`` /
``integral_cancel_routine`` / ``integral_delete_routine``).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Request
from jvspatial.api import endpoint
from pydantic import ValidationError

from app.agentive.services.routine_activity import build_routine_activity
from app.agentive.services.routine_tasks import (
    cancel_routine_task,
    delete_routine_task,
    get_routine_task,
    list_routine_tasks,
    serialize_routine,
    update_routine_task,
)
from app.api.errors import BadRequestError, MissingAuthenticationError
from app.api.utils import resolve_principal_id
from app.schemas.agentive.routines import (
    RoutineActivityResponse,
    RoutineCancelResponse,
    RoutineDeleteResponse,
    RoutineListResponse,
    RoutineResponse,
    RoutineUpdateRequest,
)
from app.services.change_event import emit_change_event


def _to_response(routine_dict: Dict[str, Any]) -> Dict[str, Any]:
    return RoutineResponse(**routine_dict).model_dump()


@endpoint("/agentive/routines", methods=["GET"], auth=True, tags=["Agentive"])
async def list_routines(
    request: Request, status: Optional[str] = None
) -> Dict[str, Any]:
    """List the caller's RoutineTask rows.

    Query ``status`` (optional): ``active`` | ``paused`` | ``completed`` |
    ``cancelled``. When omitted, cancelled rows are excluded.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    payload = await list_routine_tasks(user_id, status=status)
    return RoutineListResponse(
        routines=[RoutineResponse(**r) for r in payload["routines"]],
        total=payload["total"],
    ).model_dump()


@endpoint(
    "/agentive/routines/{routine_id}",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def get_routine(request: Request, routine_id: str) -> Dict[str, Any]:
    """Return a single owned RoutineTask."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    routine = await get_routine_task(user_id=user_id, routine_id=routine_id)
    return _to_response(serialize_routine(routine))


@endpoint(
    "/agentive/routines/{routine_id}",
    methods=["PATCH"],
    auth=True,
    tags=["Agentive"],
)
async def patch_routine(request: Request, routine_id: str) -> Dict[str, Any]:
    """Update an owned routine (instruction / cadence / pause / max_runs).

    Body is parsed from ``request.json()`` (not a typed param) so jvspatial
    does not nest the payload under a ``body`` key in its ParameterModel —
    the Background Tasks + Inbox clients send a flat ``RoutineUpdateRequest``.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    try:
        raw = await request.json()
    except Exception:
        raw = {}
    try:
        body = RoutineUpdateRequest.model_validate(raw or {})
    except ValidationError as exc:
        raise BadRequestError(
            message="Validation failed for routine update body",
            details={"errors": exc.errors()},
        ) from exc

    routine = await update_routine_task(
        user_id=user_id,
        routine_id=routine_id,
        status=body.status,
        instruction=body.instruction,
        cron=body.cron,
        timezone=body.timezone,
        max_runs=body.max_runs,
        clear_max_runs=body.clear_max_runs,
    )
    # D-05 single emission path (mirrors staging_executors._x_routine_task_update).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="routine_task.update",
        resource_type="RoutineTask",
        resource_id=routine.id,
        before=None,
        after={"id": routine.id, "status": routine.status, "cron": routine.cron},
        scope=f"workspace:{routine.workspace_id}",
    )
    return _to_response(serialize_routine(routine))


@endpoint(
    "/agentive/routines/{routine_id}/cancel",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def cancel_routine(request: Request, routine_id: str) -> Dict[str, Any]:
    """Stop an owned routine — soft tombstone + abort in-flight scheduled run."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    routine = await cancel_routine_task(user_id=user_id, routine_id=routine_id)
    # D-05 single emission path (mirrors staging_executors._x_routine_task_cancel).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="routine_task.delete",
        resource_type="RoutineTask",
        resource_id=routine.id,
        before=None,
        after={"id": routine.id, "status": routine.status},
        scope=f"workspace:{routine.workspace_id}",
    )
    return RoutineCancelResponse(id=routine.id, status="cancelled").model_dump()


@endpoint(
    "/agentive/routines/{routine_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Agentive"],
)
async def delete_routine(request: Request, routine_id: str) -> Dict[str, Any]:
    """Hard-delete an owned routine (abort in-flight run, then drop the node)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    # Capture workspace for the audit event before the node is gone.
    routine = await get_routine_task(user_id=user_id, routine_id=routine_id)
    workspace_id = routine.workspace_id
    deleted_id = await delete_routine_task(user_id=user_id, routine_id=routine_id)
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="routine_task.purge",
        resource_type="RoutineTask",
        resource_id=deleted_id,
        before={"id": deleted_id, "status": routine.status},
        after=None,
        scope=f"workspace:{workspace_id}",
    )
    return RoutineDeleteResponse(id=deleted_id, deleted=True).model_dump()


@endpoint(
    "/agentive/routines/{routine_id}/activity",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def get_routine_activity(
    request: Request, routine_id: str, limit: int = 40
) -> Dict[str, Any]:
    """Return run history + routine-origin chat turns for the activity panel."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    routine = await get_routine_task(user_id=user_id, routine_id=routine_id)
    page_limit = max(1, min(int(limit or 40), 100))
    payload = await build_routine_activity(routine, limit=page_limit)
    return RoutineActivityResponse(**payload).model_dump()
