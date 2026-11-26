"""Mission control aggregate endpoint tests."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_mission_control_snapshot(authenticated_client, test_user):
    if test_user is None:
        pytest.skip("no test_user")

    resp = await authenticated_client.get("/api/me/mission-control")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) >= {
        "workspaces",
        "apps",
        "tracks",
        "preview_entries",
        "entries_today",
        "active_tracks",
    }
    assert isinstance(body["workspaces"], list)
    assert isinstance(body["preview_entries"], list)
    assert isinstance(body["entries_today"], int)
    assert isinstance(body["active_tracks"], int)


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_mission_control_filters_exclusions_before_previews_and_counts(
    authenticated_client, test_user, test_user2
):
    from app.models.edges import (
        COLLABORATES_ON,
        CONTAINS,
        EXCLUDED_FROM,
        IS_MEMBER_OF,
        OWNS,
    )
    from app.models.nodes import App, Entry, Track
    from app.services.app_graph import catalog_app, catalog_track
    from app.services.personal_workspace import ensure_personal_workspace

    workspace = await ensure_personal_workspace(test_user2)
    await test_user.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    app = await App.create(name="Privacy smoke", workspace_id=workspace.id)
    await catalog_app(app)
    await workspace.connect(app, edge=CONTAINS)
    await test_user2.connect(app, edge=OWNS)
    await test_user.connect(app, edge=COLLABORATES_ON, role="viewer")
    track = await Track.create(title="Privacy evidence", workspace_id=workspace.id)
    await catalog_track(track)
    await app.connect(track, edge=CONTAINS)
    entries = []
    for title in ("Visible", "DENIED_HOME_SENTINEL", "Direct grant wins"):
        entry = await Entry.create(title=title, track_id=track.id)
        await track.connect(entry, edge=CONTAINS)
        entries.append(entry)
    visible, denied, direct = entries
    await test_user.connect(denied, edge=EXCLUDED_FROM)
    await test_user.connect(direct, edge=EXCLUDED_FROM)
    await test_user.connect(direct, edge=COLLABORATES_ON, role="viewer")

    response = await authenticated_client.get("/api/me/mission-control")
    assert response.status_code == 200, response.text
    data = response.json()
    ids = {e["id"] for e in data["preview_entries"]}
    assert {visible.id, direct.id} <= ids
    assert denied.id not in ids
    assert "DENIED_HOME_SENTINEL" not in response.text
    row = next(t for t in data["tracks"] if t["id"] == track.id)
    assert row["entry_count"] == 2

    app_tracks = await authenticated_client.get(f"/api/apps/{app.id}/tracks")
    assert app_tracks.status_code == 200, app_tracks.text
    assert app_tracks.json()["tracks"][0]["entry_count"] == 2
    await test_user.connect(track, edge=EXCLUDED_FROM)
    hidden_tracks = await authenticated_client.get(f"/api/apps/{app.id}/tracks")
    assert hidden_tracks.status_code == 200, hidden_tracks.text
    assert hidden_tracks.json()["tracks"] == []
    assert hidden_tracks.json()["total"] == 0


@pytest.mark.asyncio
async def test_mission_control_counts_follow_their_track_past_hidden_ones(
    authenticated_client, test_user
):
    """A hidden per-record Track must not shift counts onto the visible ones."""
    from app.models.nodes import Track

    created = []
    for title, visible, entries in (
        ("Line items", False, 3),
        ("Visible", True, 1),
    ):
        response = await authenticated_client.post(
            "/api/tracks", json={"title": title, "visibility": "private"}
        )
        assert response.status_code == 200, response.text
        track = await Track.get(response.json()["track"]["id"])
        track.nav_visible = visible
        await track.save()
        for index in range(entries):
            made = await authenticated_client.post(
                "/api/entries",
                json={"track_id": track.id, "title": f"{title} {index}", "body": "x"},
            )
            assert made.status_code == 200, made.text
        created.append(track.id)

    response = await authenticated_client.get("/api/me/mission-control")
    assert response.status_code == 200, response.text
    counts = {row["id"]: row["entry_count"] for row in response.json()["tracks"]}
    assert created[0] not in counts
    assert counts[created[1]] == 1
