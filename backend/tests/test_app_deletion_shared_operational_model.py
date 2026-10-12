"""Regression test: deleting one Track anchored from a shared template must
NOT delete the by-reference OperationalModel while sibling Tracks still use it.

Root-caused by deleting a Track while two sibling Tracks shared an
OperationalModel provisioned from the same ``(app_id, template_key)`` and
therefore shared ONE OperationalModel by reference (see
``materialize_anchor_track`` / ``_resolve_or_create_template_operational_model``
in ``operational_model_graph.py``). Deleting an unrelated sibling Track (e.g. a
duplicate/test Track cleaned up mid-session) cascaded through
``delete_track_and_nested_content`` -> ``delete_operational_model_subtree`` and
deleted the shared template OperationalModel outright, silently orphaning the
STILL-ACTIVE sibling Track's ``attached_operational_model_id`` scalar. That
Track kept its entries but every downstream ``OperationalModel.get(...)`` /
``_list_track_views`` call started returning empty, hiding its configured views.

Fix under test: ``delete_track_and_nested_content`` now checks whether any
OTHER Track still holds a ``HAS_OPERATIONAL_MODEL`` edge to the same
OperationalModel before cascading its delete — if so, it only unlinks the
Track being deleted and leaves the shared CP (and the sibling's access to
it) intact.
"""

from __future__ import annotations

import pytest

from app.models.edges import CONTAINS, HAS_OPERATIONAL_MODEL, IS_MEMBER_OF, OWNS
from app.models.nodes import App, OperationalModel, Track, User, Workspace
from app.services.app_deletion import delete_track_and_nested_content
from app.services.app_graph import (
    catalog_app,
    catalog_track,
    catalog_user,
    catalog_workspace,
    get_track_attached_operational_model,
)
from app.services.operational_model_graph import materialize_anchor_track


def _make_space_cp_manifest_with_template(template_key: str, template_name: str):
    return {
        "operational_model_schema_version": 2,
        "scope": "app",
        "package": {"slug": "t", "name": "T", "version": "1.0.0"},
        "app": {
            "tracks": [],
            "track_templates": [
                {
                    "key": template_key,
                    "name": template_name,
                    "entry_types": [
                        {
                            "key": "note",
                            "name": "Note",
                            "fields": [{"key": "title", "type": "text"}],
                        }
                    ],
                    "views": [],
                    "taxonomy": {"tag_groups": []},
                    "defaults": {},
                }
            ],
            "relations": [],
            "defaults": {},
        },
    }


async def _build_app_with_template(*, workspace_id: str, template_key: str):
    workspace = await Workspace.get(workspace_id)
    if workspace is None:
        workspace = await Workspace.create(
            id=workspace_id, kind="organization", name="Shared model fixture"
        )
    await catalog_workspace(workspace)
    owner = await User.create(display_name="Shared model owner")
    await catalog_user(owner)
    await owner.connect(workspace, edge=IS_MEMBER_OF, role="owner")
    app_node = await App.create(
        name="Shared CP App",
        workspace_id=workspace_id,
        owner_user_id=owner.id,
    )
    cp = await OperationalModel.create(
        name="Shared CP App Profile",
        scope="app",
        manifest=_make_space_cp_manifest_with_template(template_key, "Detail"),
        app_id=app_node.id,
        workspace_id=workspace_id,
        library_package=False,
    )
    await app_node.connect(cp, edge=HAS_OPERATIONAL_MODEL)
    app_node.attached_operational_model_id = cp.id
    await app_node.save()
    await workspace.connect(app_node, edge=CONTAINS)
    await catalog_app(app_node)
    await owner.connect(app_node, edge=OWNS, role="owner")
    return app_node


async def _track_inside(app_node: App, *, title: str, workspace_id: str) -> Track:
    track = await Track.create(
        title=title, owner_id=app_node.owner_user_id, workspace_id=workspace_id
    )
    await app_node.connect(track, edge=CONTAINS)
    await catalog_track(track)
    return track


@pytest.mark.asyncio
async def test_deleting_one_sibling_track_does_not_delete_shared_template_cp():
    ws = "ws-shared-cp-1"
    app_node = await _build_app_with_template(
        workspace_id=ws, template_key="pay-run-lines"
    )
    src1 = await _track_inside(app_node, title="Pay Run A", workspace_id=ws)
    src2 = await _track_inside(app_node, title="Pay Run B", workspace_id=ws)

    anchor1 = await materialize_anchor_track(
        source_track=src1, template_key="pay-run-lines", field_key="lines"
    )
    anchor2 = await materialize_anchor_track(
        source_track=src2, template_key="pay-run-lines", field_key="lines"
    )

    # Both anchor tracks share the SAME template OperationalModel by reference.
    assert (
        anchor1.attached_operational_model_id == anchor2.attached_operational_model_id
    )
    shared_cp_id = anchor1.attached_operational_model_id

    # Delete only the first anchor track (mirrors deleting a duplicate/test
    # Pay Run mid-session).
    await delete_track_and_nested_content(anchor1, actor_user_id=app_node.owner_user_id)

    # The shared OperationalModel must still exist and still resolve for the
    # sibling that is still using it.
    surviving_cp = await get_track_attached_operational_model(anchor2)
    assert surviving_cp is not None
    assert surviving_cp.id == shared_cp_id

    still_there = await OperationalModel.get(shared_cp_id)
    assert still_there is not None


@pytest.mark.asyncio
async def test_last_track_preserves_rooted_template_until_parent_app_deleted():
    ws = "ws-shared-cp-2"
    app_node = await _build_app_with_template(
        workspace_id=ws, template_key="solo-detail"
    )
    src = await _track_inside(app_node, title="Solo Source", workspace_id=ws)

    anchor = await materialize_anchor_track(
        source_track=src, template_key="solo-detail", field_key="lines"
    )
    cp_id = anchor.attached_operational_model_id
    assert cp_id

    await delete_track_and_nested_content(anchor, actor_user_id=app_node.owner_user_id)

    # The App's declared template remains rooted and available to future anchors.
    assert await OperationalModel.get(cp_id) is not None
    from app.services.app_deletion import delete_app_cascade

    await delete_app_cascade(app_node, actor_user_id=app_node.owner_user_id)
    assert await OperationalModel.get(cp_id) is None
