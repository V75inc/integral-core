"""Rooted, revision-bound model choices isolated from ordinary Workspace saves."""

from jvspatial.core.context import get_default_context

from app.models.edges import HAS_MODEL_CONFIGURATION
from app.models.nodes import Workspace, WorkspaceModelConfiguration


async def configuration_record(workspace_id: str, *, create: bool = False):
    """Read a rooted model record or create it under the caller's transaction.

    Mirrors WorkspaceAuthorizationState: a Workspace row lock serializes first
    creation. Configuration is never copied into a cached ordinary Workspace.
    Read-only defaults do not provision graph records or perform model calls.
    """
    if not workspace_id:
        return None
    context = get_default_context()
    if create:
        locked = await context.database.find_one_and_update(
            "node",
            {"id": workspace_id, "entity": "Workspace"},
            {"$inc": {"context.storage_bytes_used": 0}},
        )
        if locked is None:
            return None
    workspace = await Workspace.find_one({"id": workspace_id})
    if workspace is None:
        return None
    identity = f"n.WorkspaceModelConfiguration.{workspace_id}"
    record = await WorkspaceModelConfiguration.find_one({"id": identity})
    if record is None and not create:
        return WorkspaceModelConfiguration(id=identity, workspace_id=workspace_id)
    if record is None:
        record = await WorkspaceModelConfiguration.create(
            id=identity, workspace_id=workspace_id
        )
        await workspace.connect(record, edge=HAS_MODEL_CONFIGURATION)
    edges = await context.find_edges_between(
        workspace.id, record.id, edge_class=HAS_MODEL_CONFIGURATION
    )
    if record.workspace_id != workspace_id or len(edges) != 1:
        raise ValueError("The workspace model configuration needs repair")
    return record
