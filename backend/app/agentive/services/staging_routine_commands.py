"""Approved routine creation/edit receipts; scheduled fires have separate identity."""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any, Awaitable, Callable

from app.agentive.staging import StagingError

ROUTINE_COMMAND_KINDS = frozenset(
    {
        "routine_task_create",
        "routine_task_update",
        "routine_task_cancel",
        "routine_task_purge",
    }
)


async def _admit_thread(user_id: str, workspace_id: str, thread_id: str) -> None:
    from jvspatial.core.context import get_default_context

    from app.models.edges import CATALOGS, CONTAINS
    from app.models.nodes import ChatThread, ChatThreads
    from app.services.authority_facts import primary_node_record

    database = get_default_context().database
    thread = await primary_node_record(database, ChatThread, thread_id)
    catalogs = await database.find(
        "edge", {"entity": CATALOGS._entity_name(), "target": thread_id}, limit=2
    )
    branch = (
        await primary_node_record(database, ChatThreads, catalogs[0].get("source", ""))
        if len(catalogs) == 1
        else None
    )
    roots = (
        await database.find(
            "edge", {"entity": CONTAINS._entity_name(), "target": branch["id"]}, limit=2
        )
        if branch is not None
        else []
    )
    if (
        thread is None
        or thread["context"].get("user_id") != user_id
        or thread["context"].get("workspace_id") != workspace_id
        or thread["context"].get("archived", False)
        or branch is None
        or branch["context"].get("workspace_id") != workspace_id
        or len(roots) != 1
        or roots[0].get("source") != workspace_id
    ):
        raise StagingError(
            "routine_scope_invalid", "Routine conversation is unavailable"
        )


async def _admit_routine(
    user_id: str, workspace_id: str, routine_id: str, *, require_thread: bool = True
) -> Any:
    from jvspatial.core.context import get_default_context

    from app.agentive.edges import HAS_ROUTINE_TASK
    from app.agentive.nodes import RoutineTask
    from app.services.permissions import get_user_node

    routine = await RoutineTask.find_one({"id": routine_id})
    user = await get_user_node(user_id)
    owners = await get_default_context().database.find(
        "edge",
        {"entity": HAS_ROUTINE_TASK._entity_name(), "target": routine_id},
        limit=2,
    )
    if (
        routine is None
        or routine.user_id != user_id
        or routine.workspace_id != workspace_id
        or user is None
        or len(owners) != 1
        or owners[0].get("source") != user.id
    ):
        raise StagingError("routine_scope_invalid", "Routine is unavailable")
    if require_thread:
        await _admit_thread(user_id, workspace_id, routine.thread_id)
    return routine


async def apply_routine_command(
    *,
    user_id: str,
    workspace_id: str,
    payload: dict[str, Any],
    kind: str,
    execute: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Commit a reviewed schedule delta, event intent and exact receipt together."""
    from jvspatial.core.context import get_default_context

    from app.agentive.nodes import RoutineTask
    from app.agentive.services.staging_execution import (
        deliver_staging_command_events,
        staged_command_effect,
    )
    from app.services.entry_write_scope import (
        entry_write_scope,
        is_postgres_entry_transaction,
    )
    from app.services.identity_admission import identity_admission
    from app.services.membership_write_scope import membership_write_scope
    from app.services.release_admission import release_admission

    if kind not in ROUTINE_COMMAND_KINDS or not workspace_id:
        raise StagingError("routine_scope_invalid", "Routine workspace is required")
    stopping = kind in {"routine_task_cancel", "routine_task_purge"}
    routine = None
    scope = (
        entry_write_scope(str(payload["routine_id"]))
        if stopping
        else membership_write_scope(workspace_id, principal_ids=(user_id,))
    )
    async with scope, identity_admission((user_id,)):
        async with (
            nullcontext() if stopping else release_admission(user_id, workspace_id)
        ):
            async with staged_command_effect(
                user_id=user_id,
                workspace_id=workspace_id,
                kind=kind,
                targets=dict(payload),
            ) as complete:
                thread_id = str(payload.get("thread_id") or "")
                if kind != "routine_task_create":
                    database = get_default_context().database
                    # Lock before hydration: independent approved edits must see
                    # the committed predecessor rather than an old cached Node.
                    row = await database.find_one_and_update(
                        "node",
                        {
                            "id": payload["routine_id"],
                            "entity": RoutineTask._entity_name(),
                        },
                        {"$set": {}},
                    )
                    context = (row or {}).get("context") or {}
                    if (
                        context.get("user_id") != user_id
                        or context.get("workspace_id") != workspace_id
                    ):
                        raise StagingError(
                            "routine_scope_invalid", "Routine is unavailable"
                        )
                    routine = await _admit_routine(
                        user_id,
                        workspace_id,
                        payload["routine_id"],
                        require_thread=not stopping,
                    )
                    thread_id = routine.thread_id
                if not stopping:
                    await _admit_thread(user_id, workspace_id, thread_id)
                result = await execute()
                if complete is not None:
                    await complete(
                        {
                            "routine_id": result["id"],
                            "deleted": kind == "routine_task_purge",
                            "cancelled": kind == "routine_task_cancel",
                        }
                    )
    if is_postgres_entry_transaction(get_default_context().database):
        return result  # Existing outer transaction owns delivery/recovery.
    await deliver_staging_command_events()
    if stopping and routine is not None:
        from app.agentive.services.routine_tasks import offer_routine_interruption

        await offer_routine_interruption(routine)
    return result


async def readback(
    sc: Any, workspace_id: str, evidence: dict[str, Any]
) -> dict[str, Any]:
    """Observe current owned schedule state without repeating an approved change."""
    from app.agentive.nodes import RoutineTask
    from app.agentive.services.routine_tasks import serialize_routine
    from app.agentive.services.staging_execution import execution_record_id
    from app.api.errors import InsufficientPermissionsError
    from app.services.release_admission import release_admission

    result: dict[str, Any] = {
        "ok": True,
        "applied": True,
        "result_access": "withheld",
        "message": "This approved routine change already applied. Current content is unavailable.",
    }
    try:
        if evidence.get("deleted"):
            result.update(
                deleted=True, message="This approved routine removal already applied."
            )
        elif evidence.get("cancelled"):
            result.update(cancelled=True)
        async with release_admission(sc.user_id, workspace_id):
            routine = await RoutineTask.find_one({"id": evidence.get("routine_id", "")})
            if (
                routine is not None
                and routine.user_id == sc.user_id
                and routine.workspace_id == workspace_id
            ):
                routine = await _admit_routine(sc.user_id, workspace_id, routine.id)
                result = {
                    **serialize_routine(routine),
                    "ok": True,
                    "applied": True,
                    "message": "This approved routine change already applied. Current routine returned.",
                }
    except InsufficientPermissionsError:
        pass
    except StagingError as exc:
        if exc.code != "routine_scope_invalid":
            raise
    result["execution_receipt"] = {
        "id": execution_record_id(sc.token),
        "status": "applied",
        "replayed": True,
    }
    return result
