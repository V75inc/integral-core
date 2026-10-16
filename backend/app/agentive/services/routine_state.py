"""Fresh RoutineTask rows shared by owner edits and scheduler bookkeeping."""

from __future__ import annotations

from contextlib import asynccontextmanager, nullcontext
from typing import AsyncIterator

from app.agentive.nodes import RoutineTask
from app.services.entry_write_scope import entry_write_scope
from app.services.identity_admission import identity_admission


@asynccontextmanager
async def locked_routine(
    routine_id: str, *, principal_id: str | None = None
) -> AsyncIterator[RoutineTask | None]:
    """Read after the native row lock; retain the lock through the caller's write.

    This is a serialization seam, not a resource grant. Owner services still
    validate ownership; internal scheduler callers may only patch bookkeeping.
    Network events and interruption offers belong after this scope exits.
    """
    async with entry_write_scope(routine_id) as graph:
        async with (
            identity_admission((principal_id,)) if principal_id else nullcontext()
        ):
            row = await graph.database.find_one_and_update(
                "node",
                {"id": routine_id, "entity": RoutineTask._entity_name()},
                {"$set": {}},
            )
            yield (
                await RoutineTask.find_one({"id": routine_id})
                if row is not None
                else None
            )
