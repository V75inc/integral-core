"""Regression test: deleting one Track anchored from a shared template must
NOT delete the by-reference OperationalModel while sibling Tracks still use it.

Root-caused live in the payroll app redesign session: two Pay Run Lines
Tracks were anchor-provisioned from the same ``(app_id, template_key)`` and
therefore shared ONE OperationalModel by reference (see
``materialize_anchor_track`` / ``_resolve_or_create_template_operational_model``
in ``operational_model_graph.py``). Deleting an unrelated sibling Track (e.g. a
duplicate/test Pay Run cleaned up mid-session) cascaded through
``delete_track_and_nested_content`` -> ``delete_operational_model_subtree`` and
deleted the shared template OperationalModel outright, silently orphaning the
STILL-ACTIVE sibling Track's ``attached_operational_model_id`` scalar. That
Track kept its entries but every downstream ``OperationalModel.get(...)`` /
``_list_track_views`` call started returning empty, surfacing to the user as
"View 'payroll_register' not found on this track" with zero server-side
error at the point of actual data loss.

Fix under test: ``delete_track_and_nested_content`` now checks whether any
OTHER Track still holds a ``HAS_OPERATIONAL_MODEL`` edge to the same
OperationalModel before cascading its delete — if so, it only unlinks the
Track being deleted and leaves the shared CP (and the sibling's access to
it) intact.
"""

from __future__ import annotations

import pytest

from app.models.edges import CONTAINS, HAS_OPERATIONAL_MODEL
from app.models.nodes import App, OperationalModel, Track
from app.services.app_deletion import delete_track_and_nested_content
from app.services.app_graph import get_track_attached_operational_model
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
    app_node = await App.create(
        name="Shared CP App",
        workspace_id=workspace_id,
        owner_user_id="user-shared-cp",
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
    return app_node


async def _track_inside(app_node: App, *, title: str, workspace_id: str) -> Track:
    track = await Track.create(
        title=title, owner_id="user-shared-cp", workspace_id=workspace_id
    )
    await app_node.connect(track, edge=CONTAINS)
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
    await delete_track_and_nested_content(anchor1)

    # The shared OperationalModel must still exist and still resolve for the
    # sibling that is still using it.
    surviving_cp = await get_track_attached_operational_model(anchor2)
    assert surviving_cp is not None
    assert surviving_cp.id == shared_cp_id

    still_there = await OperationalModel.get(shared_cp_id)
    assert still_there is not None


@pytest.mark.asyncio
async def test_deleting_the_last_referencing_track_still_deletes_the_cp():
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

    await delete_track_and_nested_content(anchor)

    # No other Track referenced this CP, so it's still torn down as before —
    # the fix only guards the SHARED case, it doesn't leak orphan CPs.
    gone = await OperationalModel.get(cp_id)
    assert gone is None
