"""Owner deletion must remove populated Apps through canonical commands."""

import pytest

pytestmark = pytest.mark.smoke


async def _delete(client, app_id, channel, test_user, monkeypatch):
    if channel == "direct":
        response = await client.delete(f"/api/apps/{app_id}")
        assert response.status_code == 200, response.text
        return response.json()
    from app.agentive.services.staging_apply import bless_and_execute
    from app.agentive.staging import create_staged_change
    from app.agentive.tooling.bindings import _stage_delete_app
    from app.services.agent_scope import current_scope_workspace_id

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "77" * 32)
    app = (await client.get(f"/api/apps/{app_id}")).json()["app"]
    reset = current_scope_workspace_id.set(app["workspace_id"])
    try:
        specification = await _stage_delete_app({"app_id": app_id})
        staged = await create_staged_change(
            user_id=test_user.user_id, session_id=None, **specification
        )
        result = await bless_and_execute(user_id=test_user.user_id, token=staged.token)
    finally:
        current_scope_workspace_id.reset(reset)
    assert result["consumed"] is True, result
    assert not result["execute_result"].get("error"), result
    return result["execute_result"]


@pytest.mark.asyncio
@pytest.mark.parametrize("entries", [0, 2])
@pytest.mark.parametrize("channel", ["direct", "agent"])
@pytest.mark.parametrize("member_owner", [False, True])
async def test_owner_deletes_populated_app(
    authenticated_client, entries, channel, member_owner, test_user, monkeypatch
):
    client = authenticated_client
    response = await client.post("/api/apps", json={"name": "Disposable owner App"})
    assert response.status_code == 200, response.text
    app_id = response.json()["app"]["id"]
    dashboard = await client.post(
        f"/api/apps/{app_id}/dashboards", json={"name": "BarChart View"}
    )
    assert dashboard.status_code == 200, dashboard.text
    response = await client.post(
        "/api/tracks", json={"title": "Disposable records", "app_id": app_id}
    )
    assert response.status_code == 200, response.text
    track_id = response.json()["track"]["id"]
    entry_ids = []
    for index in range(entries):
        response = await client.post(
            "/api/entries", json={"track_id": track_id, "title": f"Record {index}"}
        )
        assert response.status_code == 200, response.text
        entry_ids.append(response.json()["entry"]["id"])
    if member_owner:
        from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF, OWNS
        from app.models.nodes import Track, User, Workspace
        from app.services.app_graph import catalog_user

        workspace_id = (await client.get(f"/api/apps/{app_id}")).json()["app"][
            "workspace_id"
        ]
        workspace = await Workspace.get(workspace_id)
        workspace.kind = "organization"
        await workspace.save()
        admin = await User.create(display_name="Separate workspace owner")
        await catalog_user(admin)
        await admin.connect(workspace, edge=OWNS)
        ctx = await workspace.get_context()
        for target in (workspace.id, track_id):
            kinds = (
                (OWNS, IS_MEMBER_OF)
                if target == workspace.id
                else (OWNS, COLLABORATES_ON)
            )
            for kind in kinds:
                for edge in await ctx.find_edges_between(
                    test_user.id, target, edge_class=kind
                ):
                    await edge.delete()
        await test_user.connect(workspace, edge=IS_MEMBER_OF, role="member")
        from app.services.permissions import can_delete_track

        assert not await can_delete_track(test_user.user_id, track_id)
        assert await Track.get(track_id) is not None
    await _delete(client, app_id, channel, test_user, monkeypatch)
    assert (await client.get(f"/api/apps/{app_id}")).status_code == 404
    assert (await client.get(f"/api/tracks/{track_id}")).status_code == 404
    for entry_id in entry_ids:
        assert (await client.get(f"/api/entries/{entry_id}")).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["direct", "agent"])
async def test_owner_deletes_app_with_legacy_entry_missing_type_edge(
    authenticated_client, channel, test_user, monkeypatch
):
    """Older records can lack the type edge while still belonging to a Track."""
    from app.models.edges import IS_OF_TYPE
    from app.models.nodes import Entry

    client = authenticated_client
    response = await client.post("/api/apps", json={"name": "Disposable legacy App"})
    assert response.status_code == 200, response.text
    app_id = response.json()["app"]["id"]
    response = await client.post(
        f"/api/apps/{app_id}/dashboards", json={"name": "BarChart View"}
    )
    assert response.status_code == 200, response.text
    response = await client.post(
        "/api/tracks", json={"title": "Legacy records", "app_id": app_id}
    )
    assert response.status_code == 200, response.text
    track_id = response.json()["track"]["id"]
    response = await client.post(
        "/api/entries", json={"track_id": track_id, "title": "Legacy entry"}
    )
    assert response.status_code == 200, response.text
    entry = await Entry.get(response.json()["entry"]["id"])
    graph = await entry.get_context()
    assert entry.type_id
    type_edges = await graph.find_edges_between(
        entry.id, entry.type_id, edge_class=IS_OF_TYPE
    )
    assert len(type_edges) == 1
    await type_edges[0].delete()
    await _delete(client, app_id, channel, test_user, monkeypatch)
    assert (await client.get(f"/api/apps/{app_id}")).status_code == 404
    assert (await client.get(f"/api/entries/{entry.id}")).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["direct", "agent"])
async def test_owner_delete_preserves_populated_shared_track(
    authenticated_client, channel, test_user, monkeypatch
):
    client = authenticated_client
    apps = []
    for name in ("Disposable App A", "Retained App B"):
        response = await client.post("/api/apps", json={"name": name})
        assert response.status_code == 200, response.text
        apps.append(response.json()["app"]["id"])
    response = await client.post("/api/tracks", json={"title": "Shared records"})
    assert response.status_code == 200, response.text
    track_id = response.json()["track"]["id"]
    response = await client.post(
        "/api/entries", json={"track_id": track_id, "title": "Retained record"}
    )
    assert response.status_code == 200, response.text
    entry_id = response.json()["entry"]["id"]
    for app_id in apps:
        response = await client.post(
            f"/api/apps/{app_id}/tracks", json={"track_id": track_id}
        )
        assert response.status_code == 200, response.text
    from app.services.resource_cascade import (
        entry_cascade_permitted,
        resource_cascade_scope,
    )

    workspace_id = (await client.get(f"/api/apps/{apps[0]}")).json()["app"][
        "workspace_id"
    ]
    async with resource_cascade_scope(
        actor_id=test_user.user_id,
        resource_type="app",
        resource_id=apps[0],
        workspace_id=workspace_id,
    ):
        assert not await entry_cascade_permitted(test_user.user_id, entry_id)
    result = await _delete(client, apps[0], channel, test_user, monkeypatch)
    if channel == "direct":
        assert result["unlinked_tracks"] == 1
    assert (await client.get(f"/api/entries/{entry_id}")).status_code == 200
    assert (await client.get(f"/api/apps/{apps[1]}")).status_code == 200


@pytest.mark.asyncio
async def test_nonowner_cannot_delete_app_with_dashboard(
    authenticated_client, second_user_client
):
    response = await authenticated_client.post(
        "/api/apps", json={"name": "Owner-only deletion"}
    )
    assert response.status_code == 200, response.text
    app_id = response.json()["app"]["id"]
    response = await authenticated_client.post(
        f"/api/apps/{app_id}/dashboards", json={"name": "Retained dashboard"}
    )
    assert response.status_code == 200, response.text
    dashboard_id = response.json()["id"]
    response = await second_user_client.delete(f"/api/apps/{app_id}")
    assert response.status_code == 403, response.text
    assert (await authenticated_client.get(f"/api/apps/{app_id}")).status_code == 200
    boards = await authenticated_client.get(f"/api/apps/{app_id}/dashboards")
    assert dashboard_id in [board["id"] for board in boards.json()["dashboards"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["direct", "agent"])
@pytest.mark.parametrize("shared_anchor", [False, True])
async def test_owner_deletes_app_with_entry_anchored_track(
    authenticated_client, channel, shared_anchor, test_user, monkeypatch
):
    from app.models.edges import ANCHORS
    from app.models.nodes import Entry, Track

    client = authenticated_client
    response = await client.post("/api/apps", json={"name": "Anchored collection App"})
    assert response.status_code == 200, response.text
    app_id = response.json()["app"]["id"]
    track_ids, entry_ids = [], []
    for index in range(2):
        response = await client.post(
            "/api/tracks", json={"app_id": app_id, "title": f"Collection {index}"}
        )
        assert response.status_code == 200, response.text
        track_ids.append(response.json()["track"]["id"])
        response = await client.post(
            "/api/entries", json={"track_id": track_ids[-1], "title": f"Record {index}"}
        )
        assert response.status_code == 200, response.text
        entry_ids.append(response.json()["entry"]["id"])
    source = await Entry.get(entry_ids[0])
    child = await Track.get(track_ids[1])
    await source.connect(child, edge=ANCHORS, field_key="details")
    if shared_anchor:
        response = await client.post("/api/apps", json={"name": "Retained anchor App"})
        assert response.status_code == 200, response.text
        other_id = response.json()["app"]["id"]
        response = await client.post(
            f"/api/apps/{other_id}/tracks", json={"track_id": child.id}
        )
        assert response.status_code == 200, response.text
    await _delete(client, app_id, channel, test_user, monkeypatch)
    assert await Track.find_one({"id": track_ids[0]}) is None
    assert await Entry.find_one({"id": entry_ids[0]}) is None
    if shared_anchor:
        assert (await client.get(f"/api/entries/{entry_ids[1]}")).status_code == 200
        assert (await client.get(f"/api/apps/{other_id}")).status_code == 200
    else:
        assert await Track.find_one({"id": track_ids[1]}) is None
        assert await Entry.find_one({"id": entry_ids[1]}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["direct", "agent"])
async def test_owner_deletes_app_with_cross_track_entry_relations(
    authenticated_client, channel, test_user, monkeypatch
):
    """A populated App may contain relations between its own Tracks."""
    from app.models.edges import REFERENCES
    from app.models.nodes import Entry

    client = authenticated_client
    response = await client.post("/api/apps", json={"name": "Related records"})
    assert response.status_code == 200, response.text
    app_id = response.json()["app"]["id"]
    for title in ("BarChart View", "Overview", "PieChart View"):
        response = await client.post(
            f"/api/apps/{app_id}/dashboards", json={"name": title}
        )
        assert response.status_code == 200, response.text
    entry_ids = []
    for title in ("Businesses", "Contacts", "Expenses", "Other"):
        response = await client.post(
            "/api/tracks", json={"app_id": app_id, "title": title}
        )
        assert response.status_code == 200, response.text
        response = await client.post(
            "/api/entries",
            json={"track_id": response.json()["track"]["id"], "title": title},
        )
        assert response.status_code == 200, response.text
        entry_ids.append(response.json()["entry"]["id"])
    expense = await Entry.get(entry_ids[2])
    business = await Entry.get(entry_ids[0])
    await expense.connect(
        business, edge=REFERENCES, field_key="business", cross_track=True
    )
    await _delete(client, app_id, channel, test_user, monkeypatch)
    assert (await client.get(f"/api/apps/{app_id}")).status_code == 404
    for entry_id in entry_ids:
        assert await Entry.find_one({"id": entry_id}) is None
