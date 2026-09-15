"""HTTP coverage for share-link API authorization."""

import uuid

import pytest
from httpx import AsyncClient

from app.services.share_links import mint_share_link


@pytest.mark.asyncio
async def test_track_share_listing_requires_owner(
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
    test_user,
):
    track_title = f"Share authz track {uuid.uuid4().hex[:8]}"
    create = await authenticated_client.post(
        "/api/tracks", json={"title": track_title, "visibility": "private"}
    )
    assert create.status_code == 200, create.text
    track_id = create.json()["track"]["id"]

    owner_id = getattr(test_user, "user_id", None) or test_user.id
    await mint_share_link(owner_id, "track", track_id, role="viewer")

    forbidden = await second_user_client.get(f"/api/tracks/{track_id}/shares")
    assert forbidden.status_code == 403, forbidden.text

    allowed = await authenticated_client.get(f"/api/tracks/{track_id}/shares")
    assert allowed.status_code == 200, allowed.text
    payload = allowed.json()
    assert "links" in payload
    assert len(payload["links"]) == 1


@pytest.mark.asyncio
async def test_non_owner_cannot_mint_share_link_http(
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
):
    track_title = f"Mint HTTP {uuid.uuid4().hex[:8]}"
    create = await authenticated_client.post(
        "/api/tracks", json={"title": track_title, "visibility": "private"}
    )
    assert create.status_code == 200, create.text
    track_id = create.json()["track"]["id"]

    forbidden = await second_user_client.post(
        f"/api/tracks/{track_id}/shares",
        json={"role": "viewer"},
    )
    assert forbidden.status_code == 403, forbidden.text


@pytest.mark.asyncio
async def test_authenticated_share_redeem_http(
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
    test_user,
):
    track_title = f"Redeem HTTP {uuid.uuid4().hex[:8]}"
    create = await authenticated_client.post(
        "/api/tracks", json={"title": track_title, "visibility": "private"}
    )
    assert create.status_code == 200, create.text
    track_id = create.json()["track"]["id"]

    owner_id = getattr(test_user, "user_id", None) or test_user.id
    minted = await mint_share_link(owner_id, "track", track_id, role="viewer")

    redeem = await second_user_client.post(
        "/api/shares/redeem",
        json={"token": minted["token"]},
    )
    assert redeem.status_code == 200, redeem.text
    body = redeem.json()
    assert body.get("resource_id") == track_id
    assert body.get("role") == "viewer"


@pytest.mark.asyncio
async def test_track_admin_collaborator_can_mint_list_and_revoke_share_links(
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
    test_user2,
):
    track_title = f"Admin collab share {uuid.uuid4().hex[:8]}"
    create = await authenticated_client.post(
        "/api/tracks", json={"title": track_title, "visibility": "private"}
    )
    assert create.status_code == 200, create.text
    track_id = create.json()["track"]["id"]

    add_admin = await authenticated_client.post(
        f"/api/tracks/{track_id}/collaborators",
        json={"collaborator_user_id": test_user2.id, "role": "admin"},
    )
    assert add_admin.status_code == 200, add_admin.text

    mint = await second_user_client.post(
        f"/api/tracks/{track_id}/shares",
        json={"role": "viewer"},
    )
    assert mint.status_code == 200, mint.text
    share_link_id = mint.json()["share_link"]["id"]

    listing = await second_user_client.get(f"/api/tracks/{track_id}/shares")
    assert listing.status_code == 200, listing.text
    assert len(listing.json()["links"]) == 1

    revoke = await second_user_client.delete(f"/api/shares/{share_link_id}")
    assert revoke.status_code == 200, revoke.text


@pytest.mark.asyncio
async def test_track_editor_collaborator_cannot_mint_share_link(
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
    test_user2,
):
    track_title = f"Editor collab share {uuid.uuid4().hex[:8]}"
    create = await authenticated_client.post(
        "/api/tracks", json={"title": track_title, "visibility": "private"}
    )
    assert create.status_code == 200, create.text
    track_id = create.json()["track"]["id"]

    add_editor = await authenticated_client.post(
        f"/api/tracks/{track_id}/collaborators",
        json={"collaborator_user_id": test_user2.id, "role": "editor"},
    )
    assert add_editor.status_code == 200, add_editor.text

    forbidden = await second_user_client.post(
        f"/api/tracks/{track_id}/shares",
        json={"role": "viewer"},
    )
    assert forbidden.status_code == 403, forbidden.text
