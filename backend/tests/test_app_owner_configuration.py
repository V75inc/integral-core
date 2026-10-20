"""App ownership configures its structure without extending collaborator roles."""

import pytest

from app.models.edges import COLLABORATES_ON, EXCLUDED_FROM, IS_MEMBER_OF, OWNS
from app.models.nodes import User
from app.services.app_graph import catalog_user
from app.services.permissions import can_admin_track, can_delete_track, resolve_role
from tests.test_resource_location import graph

pytestmark = pytest.mark.smoke


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["active", "paused", "failed", "awaiting_settings"])
async def test_app_owner_configures_track_without_a_direct_track_grant(state):
    _, workspace, app, track, _ = await graph()
    owner = await User.create(display_name="App owner")
    await catalog_user(owner)
    await owner.connect(workspace, edge=IS_MEMBER_OF, role="member")
    await owner.connect(app, edge=OWNS)
    app.lifecycle_state = state
    await app.save()
    if state == "active":
        assert await resolve_role(owner.id, "track", track.id) == "editor"
    assert await can_admin_track(owner.id, track.id)
    assert not await can_delete_track(owner.id, track.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "editor", "commenter", "viewer"])
async def test_app_collaborators_cannot_configure_child_tracks(role):
    _, workspace, app, track, _ = await graph()
    user = await User.create(display_name="App collaborator")
    await catalog_user(user)
    await user.connect(workspace, edge=IS_MEMBER_OF, role="member")
    await user.connect(app, edge=COLLABORATES_ON, role=role)
    assert not await can_admin_track(user.id, track.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["excluded", "revoked", "reparented", "uninstalled"])
async def test_owner_configuration_rechecks_current_authority(fault):
    _, workspace, app, track, _ = await graph()
    owner = await User.create(display_name="App owner")
    await catalog_user(owner)
    membership = await owner.connect(workspace, edge=IS_MEMBER_OF, role="member")
    await owner.connect(app, edge=OWNS)
    assert await can_admin_track(owner.id, track.id)
    if fault == "excluded":
        await owner.connect(track, edge=EXCLUDED_FROM)
    elif fault == "revoked":
        await membership.delete()
    elif fault == "uninstalled":
        app.lifecycle_state = "uninstalled"
        await app.save()
    else:
        from app.models.edges import CONTAINS

        for edge in await (await app.get_context()).find_edges_between(
            app.id, track.id, edge_class=CONTAINS
        ):
            await edge.delete()
    assert not await can_admin_track(owner.id, track.id)


@pytest.mark.asyncio
async def test_app_owner_can_revise_its_installed_track_model():
    _, workspace, app, track, _ = await graph()
    from app.models.nodes import OperationalModel
    from app.services.operational_model_authoring import (
        apply_patch_to_draft,
        get_or_create_draft,
    )

    owner = await User.create(display_name="Installed App owner")
    await catalog_user(owner)
    await owner.connect(workspace, edge=IS_MEMBER_OF, role="member")
    await owner.connect(app, edge=OWNS)
    model = await OperationalModel.get(track.attached_operational_model_id)
    assert model is not None
    draft = await get_or_create_draft(user_id=owner.id, operational_model_id=model.id)
    assert not draft.get("error"), draft
    result = await apply_patch_to_draft(
        user_id=owner.id,
        draft_id=draft["draft"]["id"],
        operations=[
            {
                "op": "add_entry_type",
                "spec": {"key": "extra", "name": "Extra", "fields": []},
            }
        ],
    )
    assert not result.get("error"), result
    assert result["applied_op_count"] == 1
