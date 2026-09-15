"""Tests for space and track ownership transfer."""

import pytest
from httpx import AsyncClient


async def _signup_collab(client: AsyncClient, email: str, name: str) -> str:
    r = await client.post(
        "/api/auth/signup",
        json={"email": email, "password": "testpassword123", "name": name},
        timeout=10.0,
    )
    if r.status_code not in (200, 201):
        pytest.skip("signup failed for collaborator")
    uid = (r.json().get("user") or {}).get("id")
    if not uid:
        pytest.skip("no user id from signup")
    return uid


@pytest.mark.asyncio
async def test_transfer_space_ownership_to_collaborator(
    client: AsyncClient, authenticated_client: AsyncClient
):
    collab_id = await _signup_collab(
        client, "xfer_space_collab@example.com", "App Collab"
    )
    sp = (
        await authenticated_client.post(
            "/api/apps", json={"name": "Xfer App", "description": ""}
        )
    ).json()["app"]
    sp_id = sp["id"]
    add = await authenticated_client.post(
        f"/api/apps/{sp_id}/collaborators",
        json={"collaborator_user_id": collab_id, "role": "editor"},
    )
    assert add.status_code == 200

    old_owner = sp["owner_user_id"]
    xfer = await authenticated_client.post(
        f"/api/apps/{sp_id}/transfer-ownership",
        json={"new_owner_user_id": collab_id},
    )
    assert xfer.status_code == 200, xfer.text
    data = xfer.json()
    assert data.get("message")
    assert data["app"]["owner_user_id"] != old_owner


@pytest.mark.asyncio
async def test_transfer_space_ownership_rejects_non_collaborator(
    client: AsyncClient, authenticated_client: AsyncClient
):
    stranger_id = await _signup_collab(
        client, "xfer_space_stranger@example.com", "Stranger"
    )
    sp = (
        await authenticated_client.post(
            "/api/apps", json={"name": "Xfer App 2", "description": ""}
        )
    ).json()["app"]
    sp_id = sp["id"]

    xfer = await authenticated_client.post(
        f"/api/apps/{sp_id}/transfer-ownership",
        json={"new_owner_user_id": stranger_id},
    )
    assert xfer.status_code == 400


@pytest.mark.asyncio
async def test_transfer_track_ownership(authenticated_client: AsyncClient, test_user2):
    if not test_user2 or not getattr(test_user2, "id", None):
        pytest.skip("test_user2 not available")

    tr = (
        await authenticated_client.post(
            "/api/tracks", json={"title": "Xfer Track", "visibility": "private"}
        )
    ).json()["track"]
    tid = tr["id"]
    prev_owner = tr["owner_id"]

    add = await authenticated_client.post(
        f"/api/tracks/{tid}/collaborators",
        json={"collaborator_user_id": test_user2.id, "role": "editor"},
    )
    assert add.status_code == 200

    xfer = await authenticated_client.post(
        f"/api/tracks/{tid}/transfer-ownership",
        json={"new_owner_user_id": test_user2.id},
    )
    assert xfer.status_code == 200, xfer.text
    assert xfer.json()["track"]["owner_id"] != prev_owner
