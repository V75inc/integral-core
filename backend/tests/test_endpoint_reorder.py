"""Phase 34/36 — PATCH reorder endpoints for apps and tracks."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_reorder_workspace_apps_updates_position(
    authenticated_client: AsyncClient, test_user
):
    from app.models.edges import CONTAINS, IS_MEMBER_OF, OWNS
    from app.models.nodes import App, Workspace
    from app.utils.time import utc_now_iso

    owner = test_user
    now = utc_now_iso()
    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Reorder WS",
        name_fold="reorder ws",
        created_at=now,
        updated_at=now,
    )
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=now)

    app_a = await App.create(
        name="App A",
        name_fold="app a",
        workspace_id=ws.id,
        owner_id=owner.id,
        position=0,
        created_at=now,
        updated_at=now,
    )
    app_b = await App.create(
        name="App B",
        name_fold="app b",
        workspace_id=ws.id,
        owner_id=owner.id,
        position=1,
        created_at=now,
        updated_at=now,
    )
    await owner.connect(app_a, edge=OWNS, added_at=now)
    await owner.connect(app_b, edge=OWNS, added_at=now)
    await ws.connect(app_a, edge=CONTAINS, added_at=now)
    await ws.connect(app_b, edge=CONTAINS, added_at=now)

    resp = await authenticated_client.patch(
        f"/api/workspaces/{ws.id}/apps/order",
        json={"app_ids": [app_b.id, app_a.id]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["updated"] == [app_b.id, app_a.id]

    refreshed_a = await App.get(app_a.id)
    refreshed_b = await App.get(app_b.id)
    assert refreshed_b.position == 0
    assert refreshed_a.position == 1


@pytest.mark.asyncio
async def test_reorder_app_tracks_updates_contains_edge_position(
    authenticated_client: AsyncClient, test_user
):
    from app.models.edges import COLLABORATES_ON, CONTAINS, IS_MEMBER_OF, OWNS
    from app.models.nodes import App, Track, Workspace
    from app.utils.time import utc_now_iso

    owner = test_user
    now = utc_now_iso()
    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Track Reorder WS",
        name_fold="track reorder ws",
        created_at=now,
        updated_at=now,
    )
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=now)

    app_node = await App.create(
        name="Reorder App",
        name_fold="reorder app",
        workspace_id=ws.id,
        owner_id=owner.id,
        created_at=now,
        updated_at=now,
    )
    await owner.connect(app_node, edge=OWNS, added_at=now)
    await ws.connect(app_node, edge=CONTAINS, added_at=now)

    track_a = await Track.create(
        title="Track A",
        owner_id=owner.id,
        workspace_id=ws.id,
        created_at=now,
        updated_at=now,
    )
    track_b = await Track.create(
        title="Track B",
        owner_id=owner.id,
        workspace_id=ws.id,
        created_at=now,
        updated_at=now,
    )
    await owner.connect(track_a, edge=COLLABORATES_ON, role="owner", added_at=now)
    await owner.connect(track_b, edge=COLLABORATES_ON, role="owner", added_at=now)
    edge_a = await app_node.connect(track_a, edge=CONTAINS, added_at=now, position=0)
    edge_b = await app_node.connect(track_b, edge=CONTAINS, added_at=now, position=1)

    resp = await authenticated_client.patch(
        f"/api/apps/{app_node.id}/tracks/order",
        json={"track_ids": [track_b.id, track_a.id]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated"] == [track_b.id, track_a.id]

    ctx = await app_node.get_context()
    from app.models.edges import CONTAINS as ContainsEdge

    edges_a = await ctx.find_edges_between(
        app_node.id, track_a.id, edge_class=ContainsEdge
    )
    edges_b = await ctx.find_edges_between(
        app_node.id, track_b.id, edge_class=ContainsEdge
    )
    assert edges_b[0].position == 0
    assert edges_a[0].position == 1

    # Silence unused-variable lint for edge vars created at connect time.
    assert edge_a is not None and edge_b is not None
