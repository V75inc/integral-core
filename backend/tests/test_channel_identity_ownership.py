"""ChannelIdentity verify / delete / resolve hardening.

- DELETE and the OTP-less verify never checked ``ci.user_id == caller``;
  the OTP-less verify let any user bind and self-verify someone else's phone.
- ``resolve_channel_identity`` returned ``identities[0]`` regardless of
  ``verified`` — an unverified (i.e. unproven) claim resolved to a user.
- ``POST .../identities/resolve`` accepted a plain user JWT.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

_PHONE = "+15550001111"


async def _create_identity(client: AsyncClient, channel_user_id: str = _PHONE) -> str:
    r = await client.post(
        "/api/agentive/channels/identities",
        json={"channel": "sms", "channel_user_id": channel_user_id},
    )
    assert r.status_code == 200, r.text
    return r.json()["identity"]["id"]


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_delete_identity_is_owner_only(
    authenticated_client: AsyncClient, second_user_client: AsyncClient
):
    """Cross-user DELETE is a 404; the owner can delete."""
    identity_id = await _create_identity(authenticated_client)

    r = await second_user_client.delete(
        f"/api/agentive/channels/identities/{identity_id}"
    )
    assert r.status_code == 404, r.text

    r = await authenticated_client.delete(
        f"/api/agentive/channels/identities/{identity_id}"
    )
    assert r.status_code == 200, r.text


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_otp_less_verify_is_platform_admin_only(
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
    authenticated_admin_client: AsyncClient,
):
    """OTP-less verify is admin-only and enforces address uniqueness."""
    identity_id = await _create_identity(authenticated_client)

    # Neither a stranger nor the owner may skip OTP / link-token verification.
    r = await second_user_client.post(
        f"/api/agentive/channels/identities/{identity_id}/verify"
    )
    assert r.status_code == 403, r.text
    r = await authenticated_client.post(
        f"/api/agentive/channels/identities/{identity_id}/verify"
    )
    assert r.status_code == 403, r.text

    r = await authenticated_admin_client.post(
        f"/api/agentive/channels/identities/{identity_id}/verify"
    )
    assert r.status_code == 200, r.text

    # A second account claiming the same address cannot also be verified.
    other_id = await _create_identity(second_user_client)
    r = await authenticated_admin_client.post(
        f"/api/agentive/channels/identities/{other_id}/verify"
    )
    assert r.status_code == 409, r.text


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_resolve_endpoint_requires_service_auth(
    authenticated_client: AsyncClient,
):
    """A plain user JWT cannot call identities/resolve."""
    r = await authenticated_client.post(
        "/api/agentive/channels/identities/resolve",
        json={"channel": "sms", "channel_user_id": _PHONE},
    )
    assert r.status_code == 403, r.text


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_resolve_skips_unverified_identities(
    authenticated_client: AsyncClient,
    authenticated_admin_client: AsyncClient,
    client: AsyncClient,
    test_user,
    signed_service_headers,
):
    """resolve returns nothing until the identity is verified."""
    identity_id = await _create_identity(authenticated_client)
    auth_user_id = getattr(test_user, "user_id", None) or test_user.id
    headers = signed_service_headers(auth_user_id)

    r = await client.post(
        "/api/agentive/channels/identities/resolve",
        json={"channel": "sms", "channel_user_id": _PHONE},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["resolved"] is False

    r = await authenticated_admin_client.post(
        f"/api/agentive/channels/identities/{identity_id}/verify"
    )
    assert r.status_code == 200, r.text

    r = await client.post(
        "/api/agentive/channels/identities/resolve",
        json={"channel": "sms", "channel_user_id": _PHONE},
        headers=signed_service_headers(auth_user_id),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resolved"] is True
    assert body["identity_id"] == identity_id
    assert body["verified"] is True


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_otp_verify_refuses_address_verified_for_another_account(test_user):
    """Service level: the OTP path enforces (channel, channel_user_id)
    uniqueness among VERIFIED identities."""
    from datetime import datetime

    from app.agentive.services.channel_identity import (
        create_channel_identity_with_otp,
        resolve_channel_identity,
        verify_channel_otp,
    )
    from app.models.nodes import User
    from app.services.app_graph import catalog_user

    now = datetime.now().isoformat()
    other = await User.create(
        user_id="u_other_claimant", display_name="Other", created_at=now
    )
    await catalog_user(other)

    first = await create_channel_identity_with_otp(test_user.id, "sms", "+15550002222")
    ok = await verify_channel_otp("sms", "+15550002222", first["otp_code"])
    assert ok["verified"] is True

    second = await create_channel_identity_with_otp(other.id, "sms", "+15550002222")
    denied = await verify_channel_otp("sms", "+15550002222", second["otp_code"])
    assert denied["verified"] is False
    assert "another account" in denied["message"]

    resolved = await resolve_channel_identity("sms", "+15550002222")
    assert resolved is not None
    assert resolved["identity_id"] == first["identity_id"]
