"""View ACL: refuse template rows; no CP-OR over-grant under shared CP."""

from __future__ import annotations

import pytest

from app.models.edges import CONTAINS, HAS_CONTENT_PROFILE, OWNS
from app.models.nodes import App, ContentProfile, Track, User, View
from app.services.permissions import can_edit_view, can_view_view


@pytest.mark.asyncio
async def test_template_view_without_track_id_is_denied():
    view = await View.create(
        name="Template board",
        type="kanban",
        track_id="",
        content_profile_id="cp-shared",
        is_template=True,
    )
    assert await can_view_view("user-a", view.id) is False
    assert await can_edit_view("user-a", view.id) is False


@pytest.mark.asyncio
async def test_shared_cp_admin_on_sibling_does_not_grant_edit_on_other_track_view():
    """Admin on details track A must not edit a view scoped to details track B
    merely because both share the same template ContentProfile."""
    owner = await User.create(
        email="view-acl-owner@example.com",
        username="view-acl-owner",
        hashed_password="x",
    )
    ws = "ws-view-acl"
    app_node = await App.create(
        name="Projects",
        owner_user_id=owner.id,
        workspace_id=ws,
    )
    await owner.connect(app_node, edge=OWNS)
    cp = await ContentProfile.create(
        name="project-details",
        scope="track",
        manifest={
            "content_profile_schema_version": 2,
            "scope": "track",
            "track": {"entry_types": [], "views": [], "defaults": {}},
            "materialization": {
                "kind": "space_track_template",
                "template_key": "project-details",
                "app_id": app_node.id,
            },
        },
        app_id=app_node.id,
        workspace_id=ws,
    )
    await app_node.connect(cp, edge=CONTAINS)

    track_a = await Track.create(
        title="Details A",
        owner_id=owner.id,
        workspace_id=ws,
        template_id="project-details",
        attached_content_profile_id=cp.id,
    )
    track_b = await Track.create(
        title="Details B",
        owner_id=owner.id,
        workspace_id=ws,
        template_id="project-details",
        attached_content_profile_id=cp.id,
    )
    await app_node.connect(track_a, edge=CONTAINS)
    await app_node.connect(track_b, edge=CONTAINS)
    await track_a.connect(cp, edge=HAS_CONTENT_PROFILE)
    await track_b.connect(cp, edge=HAS_CONTENT_PROFILE)

    outsider = await User.create(
        email="view-acl-outsider@example.com",
        username="view-acl-outsider",
        hashed_password="x",
    )
    # Outsider is collaborator/admin only on track A — not B.
    from app.models.edges import COLLABORATES_ON

    await outsider.connect(track_a, edge=COLLABORATES_ON, role="owner")

    view_a = await View.create(
        name="Board A",
        type="kanban",
        track_id=track_a.id,
        content_profile_id=cp.id,
    )
    view_b = await View.create(
        name="Board B",
        type="kanban",
        track_id=track_b.id,
        content_profile_id=cp.id,
    )

    assert await can_view_view(outsider.id, view_a.id) is True
    assert await can_edit_view(outsider.id, view_a.id) is True
    assert await can_view_view(outsider.id, view_b.id) is False
    assert await can_edit_view(outsider.id, view_b.id) is False
