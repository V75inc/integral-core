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
