"""Tests for POST /api/entry-lookup — batch relation-target label lookup."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_entry_lookup_returns_accessible_targets(
    authenticated_client: AsyncClient, test_user
):
    """All ids resolve to labelled targets in a single call."""
    track_resp = await authenticated_client.post(
        "/api/tracks", json={"title": "Lookup Track", "visibility": "private"}
    )
    assert track_resp.status_code == 200
    track_id = track_resp.json()["track"]["id"]

    ids = []
    for i in range(3):
        r = await authenticated_client.post(
            "/api/entries",
            json={"track_id": track_id, "title": f"Target {i}", "custom_fields": {}},
        )
        assert r.status_code == 200
        ids.append(r.json()["entry"]["id"])

    # One call resolves all three ids.
    resp = await authenticated_client.post(
        "/api/entry-lookup", json={"ids": ids, "kind": "entry"}
    )
    assert resp.status_code == 200, resp.text
    targets = resp.json()["targets"]
    assert {t["id"] for t in targets} == set(ids)
    by_id = {t["id"]: t for t in targets}
    assert by_id[ids[0]]["title"] == "Target 0"
    assert all(t["track_id"] == track_id for t in targets)
    assert all(t["track_title"] == "Lookup Track" for t in targets)


@pytest.mark.asyncio
async def test_entry_lookup_filters_unknown_and_empty(
    authenticated_client: AsyncClient, test_user
):
    """Unknown ids are filtered out and an empty request returns []."""
    # Unknown id → filtered out (not an error).
    resp = await authenticated_client.post(
        "/api/entry-lookup", json={"ids": ["n.Entry.doesnotexist"], "kind": "entry"}
    )
    assert resp.status_code == 200
    assert resp.json()["targets"] == []

    # Empty ids → empty result.
    resp = await authenticated_client.post(
        "/api/entry-lookup", json={"ids": [], "kind": "entry"}
    )
    assert resp.status_code == 200
    assert resp.json()["targets"] == []


@pytest.mark.asyncio
async def test_entry_lookup_track_kind(authenticated_client: AsyncClient, test_user):
    """kind='track' resolves track ids to their titles."""
    track_resp = await authenticated_client.post(
        "/api/tracks", json={"title": "Rel Track", "visibility": "private"}
    )
    track_id = track_resp.json()["track"]["id"]
    resp = await authenticated_client.post(
        "/api/entry-lookup", json={"ids": [track_id], "kind": "track"}
    )
    assert resp.status_code == 200, resp.text
    targets = resp.json()["targets"]
    assert len(targets) == 1
    assert targets[0]["id"] == track_id
    assert targets[0]["title"] == "Rel Track"
