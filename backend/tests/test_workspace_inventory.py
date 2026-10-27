"""Inventory totals do not require generic access to installed-App fields."""

import pytest

from app.api.errors import InsufficientPermissionsError
from app.models.edges import COLLABORATES_ON, EXCLUDED_FROM, IS_MEMBER_OF
from app.models.nodes import User, Workspace
from app.services.app_graph import catalog_user, catalog_workspace
from app.services.workspace_inventory import count_workspace_entries
from app.utils.time import utc_now_iso
from tests.test_query_boundary import _world

pytestmark = pytest.mark.smoke


@pytest.mark.asyncio
async def test_inventory_includes_packaged_apps_without_exposing_content():
    world = await _world()
    result = await count_workspace_entries(
        user_id=world.user.id, workspace_id=world.workspace.id
    )
    assert result["total_entries"] == 5
    assert result["coverage"] == "complete"
    assert set(result) == {
        "total_entries",
        "workspace_id",
        "audience",
        "coverage",
        "counted_at",
    }
    assert "secret" not in str(result)
    assert world.packaged_entry.id not in str(result)
    # A total is not authority to read packaged domain records.
    from app.services.agent_insights import query_entries

    refused = await query_entries(
        user_id=world.user.id,
        workspace_id=world.workspace.id,
        track_id=world.packaged_track.id,
    )
    assert refused["refused"]["code"] == "app_domain"


@pytest.mark.asyncio
async def test_inventory_fresh_after_record_change():
    world = await _world()
    assert (
        await count_workspace_entries(
            user_id=world.user.id, workspace_id=world.workspace.id
        )
    )["total_entries"] == 5
    await world.packaged_other.delete()
    assert (
        await count_workspace_entries(
            user_id=world.user.id, workspace_id=world.workspace.id
        )
    )["total_entries"] == 4


@pytest.mark.asyncio
async def test_inventory_cannot_widen_to_unreadable_workspace():
    world = await _world()
    with pytest.raises(InsufficientPermissionsError):
        await count_workspace_entries(user_id=world.user.id)
    now = utc_now_iso()
    foreign = await Workspace.create(
        name="Private",
        name_fold="private",
        kind="organization",
        created_at=now,
        updated_at=now,
    )
    await catalog_workspace(foreign)
    with pytest.raises(InsufficientPermissionsError):
        await count_workspace_entries(user_id=world.user.id, workspace_id=foreign.id)


@pytest.mark.asyncio
async def test_inventory_obeys_entry_exclusion_and_revocation():
    world = await _world()
    now = utc_now_iso()
    reader = await User.create(
        user_id="inventory-reader",
        display_name="Inventory Reader",
        created_at=now,
        updated_at=now,
    )
    await catalog_user(reader)
    world.workspace.kind = "organization"
    world.workspace.workspace_type = "organization"
    await world.workspace.save()
    await reader.connect(
        world.workspace, edge=IS_MEMBER_OF, role="guest", joined_at=now
    )
    await reader.connect(
        world.packaged_track, edge=COLLABORATES_ON, role="viewer", granted_at=now
    )
    assert (
        await count_workspace_entries(
            user_id=reader.id, workspace_id=world.workspace.id
        )
    )["total_entries"] == 2
    await reader.connect(world.packaged_entry, edge=EXCLUDED_FROM)
    assert (
        await count_workspace_entries(
            user_id=reader.id, workspace_id=world.workspace.id
        )
    )["total_entries"] == 1
    ctx = await reader.get_context()
    memberships = await ctx.find_edges_between(
        reader.id, world.workspace.id, edge_class=IS_MEMBER_OF
    )
    for edge in memberships:
        await edge.delete()
    with pytest.raises(InsufficientPermissionsError):
        await count_workspace_entries(
            user_id=reader.id, workspace_id=world.workspace.id
        )
