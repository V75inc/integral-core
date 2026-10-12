"""Legacy View inference proves unique graph scope and refuses ambiguous data."""

from unittest.mock import patch

import pytest

from app.exceptions import AuthorizationUnavailableError
from app.models.edges import CONTAINS, HAS_OPERATIONAL_MODEL, HAS_VIEW
from app.models.nodes import OperationalModel, Track
from app.services.app_graph import catalog_track, create_track_view
from app.services.view_scope import read_scoped_view
from app.services.view_scope_migration import (
    apply_view_scope_item,
    classify_view_scope,
    migrate_view_scopes,
)
from tests.test_resource_location import graph

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


async def legacy_view():
    owner, workspace, app, track, _ = await graph()
    view = await create_track_view(
        track, name="Legacy QA", config={"private": "configuration"}
    )
    context = await track.get_context()
    for edge in await context.find_edges_between(
        track.id, view.id, edge_class=HAS_VIEW
    ):
        await edge.delete()
    assert await read_scoped_view(view.id) is None
    return owner, workspace, app, track, view


async def test_dry_run_proves_unique_legacy_path_without_exposing_or_writing_content():
    _, workspace, _, track, view = await legacy_view()
    item = await classify_view_scope(view.id)
    assert (
        item.status == "eligible"
        and item.workspace_id == workspace.id
        and item.track_id == track.id
    )
    report = await migrate_view_scopes()
    assert report["mode"] == "dry_run"
    assert any(
        row["view_id"] == view.id and row["status"] == "eligible"
        for row in report["items"]
    )
    assert "configuration" not in str(report)
    assert await read_scoped_view(view.id) is None


async def test_shared_model_is_quarantined_even_when_scalar_names_one_valid_track():
    _, workspace, app, track, view = await legacy_view()
    model = await OperationalModel.get(track.attached_operational_model_id)
    sibling = await Track.create(
        title="Legacy shared",
        workspace_id=workspace.id,
        attached_operational_model_id=model.id,
    )
    await sibling.connect(model, edge=HAS_OPERATIONAL_MODEL)
    await app.connect(sibling, edge=CONTAINS)
    await catalog_track(sibling)
    item = await classify_view_scope(view.id)
    assert item.status == "quarantined" and item.reason == "ambiguous_model_track_scope"
    assert await read_scoped_view(view.id) is None


async def test_source_unavailability_aborts_inventory_instead_of_becoming_a_skip():
    _, _, _, _, view = await legacy_view()
    with patch(
        "app.services.view_scope_migration.primary_node_record",
        side_effect=AuthorizationUnavailableError(),
    ):
        with pytest.raises(AuthorizationUnavailableError):
            await classify_view_scope(view.id)
        with pytest.raises(AuthorizationUnavailableError):
            await migrate_view_scopes()


@pytest.mark.postgres
async def test_apply_and_rerun_are_idempotent_and_leave_one_named_scope():
    _, _, _, track, view = await legacy_view()
    result = await apply_view_scope_item(await classify_view_scope(view.id))
    assert result.status == "wired"
    assert (await read_scoped_view(view.id))[1].track_id == track.id
    rerun = await migrate_view_scopes(apply=True)
    assert any(
        row["view_id"] == view.id and row["status"] == "already_wired"
        for row in rerun["items"]
    )
    context = await track.get_context()
    edges = await context.database.find(
        "edge", {"target": view.id, "entity": HAS_VIEW._entity_name()}, limit=2
    )
    assert len(edges) == 1 and edges[0]["source"] == track.id


@pytest.mark.postgres
async def test_apply_rechecks_stale_plan_and_rolls_back_authorization_revision():
    _, workspace, _, track, view = await legacy_view()
    from app.services.authorization_scope import authorization_state_id

    context = await track.get_context()
    before = await context.database.find(
        "node", {"id": authorization_state_id(workspace.id)}, limit=1
    )
    plan = await classify_view_scope(view.id)
    await context.database.find_one_and_update(
        "node", {"id": view.id}, {"$set": {"context.track_id": "missing-track"}}
    )
    with pytest.raises(AuthorizationUnavailableError):
        await apply_view_scope_item(plan)
    after = await context.database.find(
        "node", {"id": authorization_state_id(workspace.id)}, limit=1
    )
    assert after == before
    assert await read_scoped_view(view.id) is None
    assert (
        await context.database.find(
            "edge", {"target": view.id, "entity": HAS_VIEW._entity_name()}, limit=2
        )
        == []
    )


@pytest.mark.postgres
async def test_failure_after_scope_wire_rolls_back_entire_migration_item():
    _, _, _, track, view = await legacy_view()
    plan = await classify_view_scope(view.id)
    from app.services.view_scope_migration import locate_resource as original

    async def fail_view(kind, resource_id, **kwargs):
        if kind == "view":
            raise AuthorizationUnavailableError()
        return await original(kind, resource_id, **kwargs)

    with patch(
        "app.services.view_scope_migration.locate_resource", side_effect=fail_view
    ):
        with pytest.raises(AuthorizationUnavailableError):
            await apply_view_scope_item(plan)
    assert (await classify_view_scope(view.id)).status == "eligible"
    context = await track.get_context()
    assert (
        await context.database.find(
            "edge", {"target": view.id, "entity": HAS_VIEW._entity_name()}, limit=2
        )
        == []
    )
