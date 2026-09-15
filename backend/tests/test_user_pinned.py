"""N2: per-user pinned tracks/apps — atomic toggle endpoint."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_pinned_empty_by_default(authenticated_client: AsyncClient):
    r = await authenticated_client.get("/api/users/me/pinned")
    assert r.status_code == 200
    body = r.json()
    assert body == {"pinned": {"tracks": [], "apps": []}}


@pytest.mark.asyncio
async def test_pin_and_unpin_track(authenticated_client: AsyncClient):
    track_id = "n.Track.fake-1"
    pinned = await authenticated_client.post(
        "/api/users/me/pinned",
        json={"kind": "track", "id": track_id, "pinned": True},
    )
    assert pinned.status_code == 200, pinned.text
    assert track_id in pinned.json()["pinned"]["tracks"]

    again = await authenticated_client.post(
        "/api/users/me/pinned",
        json={"kind": "track", "id": track_id, "pinned": True},
    )
    # Idempotent — list shouldn't grow on re-pin.
    assert again.json()["pinned"]["tracks"].count(track_id) == 1

    unpinned = await authenticated_client.post(
        "/api/users/me/pinned",
        json={"kind": "track", "id": track_id, "pinned": False},
    )
    assert track_id not in unpinned.json()["pinned"]["tracks"]


@pytest.mark.asyncio
async def test_pin_rejects_unknown_kind(authenticated_client: AsyncClient):
    bad = await authenticated_client.post(
        "/api/users/me/pinned",
        json={"kind": "widget", "id": "x", "pinned": True},
    )
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_pin_track_and_app_buckets_independent(
    authenticated_client: AsyncClient,
):
    track_pin = await authenticated_client.post(
        "/api/users/me/pinned",
        json={"kind": "track", "id": "t1", "pinned": True},
    )
    assert track_pin.status_code == 200, track_pin.text
    app_pin = await authenticated_client.post(
        "/api/users/me/pinned",
        json={"kind": "app", "id": "s1", "pinned": True},
    )
    assert app_pin.status_code == 200, app_pin.text

    r = await authenticated_client.get("/api/users/me/pinned")
    assert r.status_code == 200, r.text
    body = r.json()["pinned"]
    assert "t1" in body["tracks"]
    assert "s1" in body["apps"]
    assert "t1" not in body["apps"]
    assert "s1" not in body["tracks"]
    assert "spaces" not in body


@pytest.mark.asyncio
async def test_pin_legacy_space_kind_maps_to_apps_bucket(
    authenticated_client: AsyncClient,
):
    """Pre–APP-RENAME-01 clients sent kind=space; ids land in apps."""
    legacy = await authenticated_client.post(
        "/api/users/me/pinned",
        json={"kind": "space", "id": "s-legacy", "pinned": True},
    )
    assert legacy.status_code == 200, legacy.text
    body = legacy.json()["pinned"]
    assert "s-legacy" in body["apps"]
    assert "s-legacy" not in body.get("tracks", [])
    assert "spaces" not in body
