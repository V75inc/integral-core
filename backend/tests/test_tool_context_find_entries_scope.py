"""Entry search is scoped through canonical containment, not a scalar cache."""

import pytest

from app.models.edges import CONTAINS, IS_MEMBER_OF
from app.models.nodes import Entry, Workspace
from app.services.app_graph import catalog_workspace
from app.services.hooks.registry import ToolContext
from tests.test_resource_location import graph, sibling


@pytest.mark.asyncio
async def test_find_entries_keeps_only_in_workspace():
    user, workspace, _, track, first = await graph()
    second = await Entry.create(title="Second", track_id=track.id)
    await track.connect(second, edge=CONTAINS)
    other = await Workspace.create(name="Other searchable workspace")
    await catalog_workspace(other)
    await user.connect(other, edge=IS_MEMBER_OF, role="owner")
    _, _, foreign = await sibling(user, other)
    # The caller has legitimate foreign access; the execution scope still wins.
    elsewhere = ToolContext(user_id=user.id, workspace_id=other.id, scope="tool")
    assert (await elsewhere.get_entry(foreign.id)).id == foreign.id
    context = ToolContext(user_id=user.id, workspace_id=workspace.id, scope="tool")
    assert {row.id for row in await context.find_entries({})} == {first.id, second.id}
