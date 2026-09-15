"""HTTP coverage for unified /access endpoints."""

import uuid

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_track_access_snapshot_requires_membership(
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
    test_user2,
):
    create = await authenticated_client.post(
        "/api/tracks",
        json={"title": f"Access Track {uuid.uuid4().hex[:8]}", "visibility": "private"},
    )
    assert create.status_code == 200, create.text
    track_id = create.json()["track"]["id"]

    forbidden = await second_user_client.get(f"/api/tracks/{track_id}/access")
    assert forbidden.status_code == 403, forbidden.text

    add = await authenticated_client.post(
        f"/api/tracks/{track_id}/collaborators",
        json={"collaborator_user_id": test_user2.id, "role": "viewer"},
    )
    assert add.status_code == 200, add.text

    allowed = await second_user_client.get(f"/api/tracks/{track_id}/access")
    assert allowed.status_code == 200, allowed.text
    body = allowed.json()
    assert body["resource_type"] == "track"
    assert body["resource_id"] == track_id
    # Wave 1: only owner/admin see the full access graph. This caller is a
    # viewer collaborator, so the snapshot is reduced to their own effective
    # role — a viewer must not be able to enumerate who else has access.
    assert body["effective_role"] == "viewer"
    assert body.get("links_visible") is False
    for managers_only in ("direct", "inherited", "excluded", "links"):
        assert (
            managers_only not in body
        ), f"{managers_only} leaked to a non-manager in the access snapshot"

    owner_view = await authenticated_client.get(f"/api/tracks/{track_id}/access")
    assert owner_view.status_code == 200, owner_view.text
    owner_body = owner_view.json()
    assert owner_body.get("links_visible") is True
    # `effective_role` must appear in BOTH shapes. It used to be returned only
    # on the reduced branch, so a caller had to branch on the key's presence to
    # read its own standing — and the docstring claimed otherwise. Found by
    # smoke-testing the live endpoint, not by the suite.
    assert owner_body.get("effective_role") == "owner", owner_body


@pytest.mark.asyncio
async def test_app_access_snapshot_requires_membership(
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
    test_user2,
):
    app_name = f"Access App {uuid.uuid4().hex[:8]}"
    create = await authenticated_client.post(
        "/api/apps", json={"name": app_name, "visibility": "private"}
    )
    assert create.status_code == 200, create.text
    app_id = create.json()["app"]["id"]

    forbidden = await second_user_client.get(f"/api/apps/{app_id}/access")
    assert forbidden.status_code == 403, forbidden.text

    add = await authenticated_client.post(
        f"/api/apps/{app_id}/collaborators",
        json={"collaborator_user_id": test_user2.id, "role": "viewer"},
    )
    assert add.status_code == 200, add.text

    allowed = await second_user_client.get(f"/api/apps/{app_id}/access")
    assert allowed.status_code == 200, allowed.text
    body = allowed.json()
    assert body["resource_type"] == "app"
    assert body.get("links_visible") is False

    owner_view = await authenticated_client.get(f"/api/apps/{app_id}/access")
    assert owner_view.status_code == 200, owner_view.text
    assert owner_view.json().get("links_visible") is True
