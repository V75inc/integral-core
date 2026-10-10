"""Departure must preserve ownership and membership when cleanup fails."""

from unittest.mock import AsyncMock

import pytest

from app.api.errors import ResourceNotFoundError
from app.models.edges import (
    COLLABORATES_ON,
    CONTAINS,
    EXCLUDED_FROM,
    IS_MEMBER_OF,
    OWNS,
)
from app.models.nodes import App, Entry, Track, User, Workspace
from app.services import workspace_departure
from app.services.app_graph import (
    catalog_app,
    catalog_track,
    catalog_user,
    catalog_workspace,
    ensure_integral_app_graph,
)
from app.services.ownership_transfer import reassign_departing_member_ownership
from app.services.sharing import revoke_workspace_resource_grants

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


async def departure_graph():
    await ensure_integral_app_graph(include_library=False)
    owner = await User.create(user_id="departure-owner")
    await catalog_user(owner)
    member = await User.create(user_id="departure-member")
    await catalog_user(member)
    workspace = await Workspace.create(name="Departure", name_fold="departure")
    await catalog_workspace(workspace)
    await owner.connect(workspace, edge=IS_MEMBER_OF, role="owner")
    await member.connect(workspace, edge=IS_MEMBER_OF, role="member")
    app = await App.create(
        name="Member App",
        name_fold="member app",
        workspace_id=workspace.id,
        owner_user_id=member.id,
    )
    await catalog_app(app)
    await workspace.connect(app, edge=CONTAINS)
    await member.connect(app, edge=OWNS)
    track = await Track.create(
        title="Member Track",
        title_fold="member track",
        workspace_id=workspace.id,
        owner_id=member.id,
    )
    await app.connect(track, edge=CONTAINS)
    await member.connect(track, edge=OWNS)
    entry = await Entry.create(title="Shared Entry", track_id=track.id)
    await track.connect(entry, edge=CONTAINS)
    await member.connect(entry, edge=COLLABORATES_ON, role="viewer")
    await member.connect(entry, edge=EXCLUDED_FROM)
    return owner, member, workspace, app, track, entry


async def test_departure_transfers_owned_resources_and_revokes_entry_grants():
    owner, member, workspace, app, track, entry = await departure_graph()
    assert await workspace_departure.remove_workspace_membership(
        member=member,
        workspace_id=workspace.id,
    )
    context = await member.get_context()
    assert not await context.find_edges_between(
        member.id, workspace.id, edge_class=IS_MEMBER_OF
    )
    for resource in (app, track):
        assert await context.find_edges_between(owner.id, resource.id, edge_class=OWNS)
        assert not await context.find_edges_between(
            member.id, resource.id, edge_class=OWNS
        )
    assert (await App.get(app.id)).owner_user_id == owner.id
    assert (await Track.get(track.id)).owner_id == owner.id
    for edge_class in (COLLABORATES_ON, EXCLUDED_FROM):
        assert not await context.find_edges_between(
            member.id, entry.id, edge_class=edge_class
        )


async def test_cleanup_failure_keeps_membership(monkeypatch):
    _, member, workspace, _, _, _ = await departure_graph()
    monkeypatch.setattr(
        workspace_departure,
        "revoke_workspace_resource_grants",
        AsyncMock(side_effect=RuntimeError("grant store unavailable")),
    )
    with pytest.raises(RuntimeError, match="grant store unavailable"):
        await workspace_departure.remove_workspace_membership(
            member=member,
            workspace_id=workspace.id,
        )
    context = await member.get_context()
    assert await context.find_edges_between(
        member.id, workspace.id, edge_class=IS_MEMBER_OF
    )


async def test_missing_replacement_owner_never_deletes_owned_edges(monkeypatch):
    _, member, workspace, app, _, _ = await departure_graph()
    monkeypatch.setattr(
        "app.services.workspace_permissions.get_workspace_owner_user_id",
        AsyncMock(return_value=None),
    )
    with pytest.raises(ResourceNotFoundError):
        await reassign_departing_member_ownership(member, workspace.id)
    context = await member.get_context()
    assert await context.find_edges_between(member.id, app.id, edge_class=OWNS)


async def test_walker_lookup_failure_is_not_partial_success(monkeypatch):
    _, member, workspace, _, _, _ = await departure_graph()
    monkeypatch.setattr(
        User, "nodes", AsyncMock(side_effect=RuntimeError("lookup failed"))
    )
    with pytest.raises(RuntimeError, match="Workspace grant traversal failed"):
        await revoke_workspace_resource_grants(member, workspace.id)


async def test_other_workspace_grants_and_ownership_survive_departure():
    _, member, workspace, _, _, _ = await departure_graph()
    other = await Workspace.create(name="Other", name_fold="other")
    await catalog_workspace(other)
    await member.connect(other, edge=IS_MEMBER_OF, role="owner")
    track = await Track.create(
        title="Other Track", title_fold="other track", workspace_id=other.id
    )
    await catalog_track(track)
    await other.connect(track, edge=CONTAINS)
    await member.connect(track, edge=OWNS)
    await member.connect(track, edge=COLLABORATES_ON, role="editor")
    await workspace_departure.remove_workspace_membership(
        member=member, workspace_id=workspace.id
    )
    context = await member.get_context()
    for edge_class in (OWNS, COLLABORATES_ON):
        assert await context.find_edges_between(
            member.id, track.id, edge_class=edge_class
        )
