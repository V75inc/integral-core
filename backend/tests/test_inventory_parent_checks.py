"""Share candidate-discovery work without caching permission grants."""

import pytest

from app.models.edges import COLLABORATES_ON, CONTAINS, IS_MEMBER_OF
from app.models.nodes import Track, User
from app.services import permissions
from app.services.app_graph import catalog_track, catalog_user
from tests.test_resource_location import graph


@pytest.mark.asyncio
async def test_inventory_checks_parent_once_and_rechecks_after_revocation(monkeypatch):
    """Deduplicate discovery while revocations remain visible on the next read."""
    owner, workspace, app, original_track, entry = await graph()
    reader = await User.create(display_name="Inventory reader")
    await catalog_user(reader)
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    grant = await reader.connect(app, edge=COLLABORATES_ON, role="viewer")
    ids = set()
    for i in range(4):
        track = await Track.create(
            title=f"Inventory {i}",
            workspace_id=workspace.id,
            template_id="inventory-test",
            visibility="private",
        )
        await catalog_track(track)
        await app.connect(track, edge=CONTAINS)
        ids.add(track.id)

    actual = permissions.can_view_app
    calls = []

    async def counted(user_id, app_id):
        calls.append(app_id)
        return await actual(user_id, app_id)

    monkeypatch.setattr(permissions, "can_view_app", counted)
    visible = await permissions.get_user_accessible_tracks(owner.id)
    assert ids <= {track.id for track in visible}
    assert calls.count(app.id) == 1
    visible = await permissions.get_user_accessible_tracks(reader.id)
    assert ids <= {track.id for track in visible}

    # A second inventory must read current authority, even in the same request.
    await grant.delete()
    visible = await permissions.get_user_accessible_tracks(reader.id)
    assert not ids.intersection(track.id for track in visible)


@pytest.mark.asyncio
async def test_candidate_parent_check_cannot_admit_an_uninstalled_app(monkeypatch):
    """Fresh canonical admission rejects stale candidate visibility."""
    owner, workspace, app, track, entry = await graph()
    track.template_id = "inventory-test"
    await track.save()

    async def stale_candidate(user_id, app_id):
        app.lifecycle_state = "uninstalled"
        await app.save()
        return True

    monkeypatch.setattr(permissions, "can_view_app", stale_candidate)
    visible = await permissions.get_user_accessible_tracks(owner.id)
    assert track.id not in {item.id for item in visible}
