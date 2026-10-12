"""Ownership HTTP proposes exact consent; immediate service bypass stays closed."""

import uuid

import pytest
from httpx import AsyncClient

from app.api.errors import ResourceConflictError
from app.models.nodes import App, Track
from app.services.email_identity import current_account_email
from app.services.ownership_transfer import (
    transfer_app_ownership,
    transfer_track_ownership,
)
from app.services.permissions import get_user_node


async def _signup_verified_subject(client: AsyncClient) -> str:
    response = await client.post(
        "/api/auth/signup",
        json={
            "email": f"ownership-{uuid.uuid4().hex}@example.com",
            "password": "testpassword123",
            "name": "Ownership consent QA",
        },
        timeout=10,
    )
    assert response.status_code in (200, 201), response.text
    identity = response.json()["user"]["id"]
    user = await get_user_node(identity)
    assert user is not None
    email = await current_account_email(user)
    assert email
    # Server fixture establishes exact proof; live OTP qualification is separate.
    user.email_verified = True
    user.verified_email = email
    await user.save()
    return user.id


@pytest.mark.asyncio
async def test_transfer_space_ownership_proposes_without_owner_change(
    client: AsyncClient, authenticated_client: AsyncClient
):
    recipient = await _signup_verified_subject(client)
    created = await authenticated_client.post(
        "/api/apps", json={"name": "Consent App", "description": ""}
    )
    assert created.status_code == 200, created.text
    app = created.json()["app"]
    add = await authenticated_client.post(
        f"/api/apps/{app['id']}/collaborators",
        json={"collaborator_user_id": recipient, "role": "editor"},
    )
    assert add.status_code == 200, add.text
    response = await authenticated_client.post(
        f"/api/apps/{app['id']}/transfer-ownership",
        json={"new_owner_user_id": recipient},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "pending"
    assert response.json()["recipient_id"] == recipient
    assert (await App.find_one({"id": app["id"]})).owner_user_id == app["owner_user_id"]


@pytest.mark.asyncio
async def test_transfer_space_ownership_rejects_destination_without_membership(
    client: AsyncClient, authenticated_client: AsyncClient
):
    stranger = await _signup_verified_subject(client)
    created = await authenticated_client.post(
        "/api/apps", json={"name": "Consent Stranger App", "description": ""}
    )
    assert created.status_code == 200, created.text
    app = created.json()["app"]
    response = await authenticated_client.post(
        f"/api/apps/{app['id']}/transfer-ownership",
        json={"new_owner_user_id": stranger},
    )
    assert response.status_code == 400, response.text
    assert (await App.find_one({"id": app["id"]})).owner_user_id == app["owner_user_id"]


@pytest.mark.asyncio
async def test_transfer_track_ownership_proposes_without_owner_change(
    client: AsyncClient, authenticated_client: AsyncClient
):
    recipient = await _signup_verified_subject(client)
    created = await authenticated_client.post(
        "/api/tracks", json={"title": "Consent Track", "visibility": "private"}
    )
    assert created.status_code == 200, created.text
    track = created.json()["track"]
    added = await authenticated_client.post(
        f"/api/tracks/{track['id']}/collaborators",
        json={"collaborator_user_id": recipient, "role": "editor"},
    )
    assert added.status_code == 200, added.text
    response = await authenticated_client.post(
        f"/api/tracks/{track['id']}/transfer-ownership",
        json={"new_owner_user_id": recipient},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "pending"
    assert (await Track.find_one({"id": track["id"]})).owner_id == track["owner_id"]


@pytest.mark.asyncio
async def test_legacy_direct_transfer_cannot_bypass_recipient_consent():
    for function, field in (
        (transfer_app_ownership, "app_id"),
        (transfer_track_ownership, "track_id"),
    ):
        with pytest.raises(ResourceConflictError, match="consent"):
            await function(
                **{
                    field: "opaque-target",
                    "acting_user_id": "opaque-actor",
                    "new_owner_user_id": "opaque-recipient",
                }
            )
