"""Actual PostgreSQL departure commits or rolls back ownership and grants together."""

import pytest

from app.services import workspace_departure
from app.services.app_operations.transaction_scope import _transaction_database
from tests.test_workspace_departure import (
    departure_graph,
)
from tests.test_workspace_departure import (
    test_departure_transfers_owned_resources_and_revokes_entry_grants as departure_success,
)

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def test_workspace_departure_commits_complete_cleanup():
    await departure_success()


async def test_workspace_departure_rolls_back_transfers_and_grants(monkeypatch):
    owner, member, workspace, app, track, entry = await departure_graph()
    context = await member.get_context()
    database = _transaction_database(context.database)
    node_ids = [member.id, app.id, track.id, entry.id]
    before_nodes = [await database.get("node", identity) for identity in node_ids]
    query = {"source": {"$in": [owner.id, member.id]}}
    before_edges = await database.find("edge", query, sort=[("id", 1)])
    original = workspace_departure.revoke_workspace_resource_grants

    async def fail_after_grants(user, workspace_id):
        await original(user, workspace_id)
        raise RuntimeError("departure cleanup interrupted")

    monkeypatch.setattr(
        workspace_departure, "revoke_workspace_resource_grants", fail_after_grants
    )
    with pytest.raises(RuntimeError, match="departure cleanup interrupted"):
        await workspace_departure.remove_workspace_membership(
            member=member, workspace_id=workspace.id
        )
    # Read the physical store instead of the parent GraphContext's cache.
    assert [
        await database.get("node", identity) for identity in node_ids
    ] == before_nodes
    assert await database.find("edge", query, sort=[("id", 1)]) == before_edges
