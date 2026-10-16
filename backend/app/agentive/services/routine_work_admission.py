"""Live routine state is part of a scheduled WorkItem's effect authority."""

from __future__ import annotations

from typing import Any

from jvspatial.core.context import get_default_context

from app.agentive.nodes import RoutineTask
from app.schemas.agentive.work import WorkError


def _check_current(item: Any, row: dict | None) -> None:
    if row is None:
        raise WorkError("work.cancelled", "Routine was removed")
    fields = row.get("context") or {}
    if any(
        fields.get(field) != expected
        for field, expected in (
            ("user_id", item.principal_id),
            ("workspace_id", item.workspace_id),
            ("thread_id", item.thread_id),
        )
    ):
        raise WorkError("work.policy_denied", "Routine execution binding changed")
    if fields.get("status") != "active":
        raise WorkError("work.cancelled", "Routine is no longer active")
    from app.agentive.services.routine_mandate import routine_mandate_fingerprint

    expected = (item.input_payload or {}).get("routine_mandate_fingerprint")
    if not isinstance(expected, str) or expected != routine_mandate_fingerprint(fields):
        raise WorkError(
            "work.policy_denied", "Routine mandate changed or requires reconciliation"
        )


async def assert_routine_work_current(item: Any, *, lock: bool = False) -> None:
    """Order scheduled effects with stop/remove; unrelated WorkKinds are unchanged."""
    if item.kind != "routine_turn":
        return
    routine_id = str((item.input_payload or {}).get("routine_id") or "")
    if not routine_id:
        raise WorkError("work.policy_denied", "Routine work has no bound schedule")
    database = get_default_context().database
    query = {"id": routine_id, "entity": RoutineTask._entity_name()}
    _check_current(item, await database.find_one("node", query))
    from app.services.identity_admission import identity_admission

    # Match stop/edit ordering: identity rows precede the RoutineTask row. All
    # native row locks remain held by the enclosing effect transaction. The
    # initial read rejects a mismatched saved principal before admitting it.
    async with identity_admission((item.principal_id,)):
        if lock:
            row = await database.find_one_and_update("node", query, {"$set": {}})
            _check_current(item, row)
