"""Read authority does not imply access-directory authority (AC-15)."""

import pytest

from app.api.apps import list_app_collaborators
from app.api.errors import InsufficientPermissionsError
from app.api.tracks import list_collaborators, list_mention_candidates
from app.api.workspaces import list_workspace_members
from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
from app.models.nodes import User
from app.services.app_graph import catalog_user
from tests.test_access_perimeter_regressions import rooted_graph
from tests.test_model_api_authority import request_for

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


async def test_reader_cannot_enumerate_legacy_access_but_owner_can():
    owner, reader, workspace, app, track, _ = await rooted_graph()
    for function, resource in [
        (list_app_collaborators, app),
        (list_collaborators, track),
    ]:
        reduced = await function(request_for(reader.id, workspace.id), resource.id)
        assert reduced["collaborators"] == [] and reduced["directory_visible"] is False
        allowed = await function(request_for(owner.id, workspace.id), resource.id)
        assert allowed["collaborators"]
        assert allowed["caller_role"] == "owner"
        assert all("preferences" not in row for row in allowed["collaborators"])


@pytest.mark.parametrize("role", ["editor", "commenter", "viewer"])
async def test_app_member_retains_own_role_without_directory_access(role):
    from app.services.permissions import can_edit_app

    _, reader, workspace, app, _, _ = await rooted_graph()
    context = await app.get_context()
    [grant] = await context.find_edges_between(
        reader.id, app.id, edge_class=COLLABORATES_ON
    )
    grant.role = role
    await grant.save()
    response = await list_app_collaborators(
        request_for(reader.id, workspace.id), app.id
    )
    assert response["collaborators"] == []
    assert response["directory_visible"] is False
    assert response["caller_role"] == role
    assert await can_edit_app(reader.id, app.id) is (role == "editor")


async def test_guest_member_pool_is_restricted_to_administration():
    owner, reader, workspace, _, _, _ = await rooted_graph()
    with pytest.raises(InsufficientPermissionsError):
        await list_workspace_members(request_for(reader.id, workspace.id), workspace.id)
    allowed = await list_workspace_members(
        request_for(owner.id, workspace.id), workspace.id
    )
    assert allowed["members"] and all(
        "preferences" not in m for m in allowed["members"]
    )


async def test_public_track_mention_picker_cannot_search_global_users():
    owner, reader, workspace, _, track, _ = await rooted_graph()
    track.visibility = "public"
    await track.save()
    foreign = await User.create(display_name="Foreign directory secret")
    await catalog_user(foreign)
    peer = await User.create(display_name="Local peer")
    await catalog_user(peer)
    await peer.connect(workspace, edge=IS_MEMBER_OF, role="member")
    await peer.connect(track, edge=COLLABORATES_ON, role="viewer")
    result = await list_mention_candidates(
        request_for(owner.id, workspace.id), track.id, q="", limit=50
    )
    ids = {u["id"] for u in result["users"]}
    assert foreign.id not in ids and peer.id in ids
    assert all(
        "email" not in row and "preferences" not in row for row in result["users"]
    )
