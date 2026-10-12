"""Canonical creators return usable Nodes without escaping outer transactions."""

import pytest
from jvspatial.core.context import get_default_context

from app.models.nodes import App, Track
from app.services.app_operations.transaction_scope import postgres_graph_transaction
from app.services.app_service import create_app_for_user
from app.services.personal_workspace import ensure_personal_workspace
from app.services.track_service import create_track_in_space

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.contract,
    pytest.mark.postgres,
    pytest.mark.asyncio,
]


async def _create(kind, owner, workspace):
    if kind == "app":
        return await create_app_for_user(
            owner.id, "Creation context App", workspace_id=workspace.id
        )
    return await create_track_in_space(
        owner.id, title="Creation context Track", workspace_id=workspace.id
    )


@pytest.mark.parametrize("kind", ["app", "track"])
async def test_created_node_can_be_written_after_creator_transaction(kind, test_user):
    workspace = await ensure_personal_workspace(test_user)
    node = await _create(kind, test_user, workspace)
    assert await node.get_context() is get_default_context()
    field = "description" if kind == "app" else "purpose"
    setattr(node, field, "Write after creator commit")
    await node.save()
    model = App if kind == "app" else Track
    current = await model.find_one({"id": node.id})
    assert current is not None
    assert getattr(current, field) == "Write after creator commit"


@pytest.mark.parametrize("kind", ["app", "track"])
async def test_created_node_stays_inside_outer_transaction_rollback(kind, test_user):
    workspace = await ensure_personal_workspace(test_user)

    class RollbackProbe(Exception):
        """Force the enclosing operation to abort after a follow-up write."""

    with pytest.raises(RollbackProbe):
        async with postgres_graph_transaction():
            context = get_default_context()
            node = await _create(kind, test_user, workspace)
            assert await node.get_context() is context
            field = "description" if kind == "app" else "purpose"
            setattr(node, field, "Must roll back with outer operation")
            await node.save()
            raise RollbackProbe()
    model = App if kind == "app" else Track
    assert await model.find_one({"id": node.id}) is None
