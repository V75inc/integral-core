"""Transactional change barrier for derived workspace index verification.

This is a scalar Object record, never graph authority (I-GRAPH-02). Writers
lock it before selecting serving/building profiles and increment only when
replacing durable intent. Activation locks the same row and compares the
verified epoch. An allocated timestamp or an empty queue is not a boundary.
"""

from jvspatial.core.context import get_default_context

from .index_definition import digest


async def barrier(workspace_id: str, *, lock: bool = False) -> int:
    """Read, or transactionally lock, a workspace's committed intent epoch."""
    if not workspace_id:
        return 0
    database = get_default_context().database
    identity = "o.WorkspaceIndexBarrier." + digest(workspace_id)
    if lock:
        await database.insert_if_absent(
            "object",
            {
                "id": identity,
                "entity": "WorkspaceIndexBarrier",
                "context": {"workspace_id": workspace_id, "epoch": 0},
            },
        )
        row = await database.find_one_and_update(
            "object", {"id": identity}, {"$inc": {"context.epoch": 0}}
        )
    else:
        row = await database.get("object", identity)
    return int((row or {}).get("context", {}).get("epoch", 0))


async def advance(workspace_id: str, *, scope: bool = False) -> int:
    """Advance under the caller's write transaction after retaining intent."""
    if workspace_id:
        database = get_default_context().database
        row = await database.find_one_and_update(
            "object",
            {"id": "o.WorkspaceIndexBarrier." + digest(workspace_id)},
            {"$inc": {"context.epoch": 1}},
        )
        epoch = int(row["context"]["epoch"])
        if scope:
            await database.find_one_and_update(
                "object", {"id": row["id"]}, {"$set": {"context.scope_epoch": epoch}}
            )
        return epoch
    return 0


async def scope_epoch(workspace_id: str) -> int:
    """Read the last committed declaration-wide invalidation boundary."""
    row = await get_default_context().database.get(
        "object", "o.WorkspaceIndexBarrier." + digest(workspace_id)
    )
    return int((row or {}).get("context", {}).get("scope_epoch", 0))
