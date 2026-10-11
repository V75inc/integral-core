"""Cached and coerced mailbox proof cannot confer authority."""

import pytest
from jvspatial.api.auth.models import User as AuthUser
from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError
from app.models.nodes import User
from app.services.app_graph import catalog_user
from app.services.email_identity import verified_current_email

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


async def account():
    identity = await AuthUser.create(
        email="primary-proof@example.com", password_hash="fixture", is_active=True
    )
    user = await User.create(
        user_id=identity.id, email_verified=True, verified_email=identity.email
    )
    await catalog_user(user)
    return identity, user


@pytest.mark.parametrize(
    "field,value", [("email_verified", False), ("verified_email", "other@example.com")]
)
async def test_primary_proof_reduction_beats_warm_profile(field, value):
    _, user = await account()
    assert await verified_current_email(user) == "primary-proof@example.com"
    await get_default_context().database.find_one_and_update(
        "node", {"id": user.id}, {"$set": {f"context.{field}": value}}
    )
    assert await verified_current_email(user) is None


async def test_coerced_proof_flag_is_unavailable():
    _, user = await account()
    await get_default_context().database.find_one_and_update(
        "node", {"id": user.id}, {"$set": {"context.email_verified": "true"}}
    )
    with pytest.raises(AuthorizationUnavailableError):
        await verified_current_email(user)


async def test_warm_auth_email_cannot_reuse_old_proof():
    identity, user = await account()
    assert await verified_current_email(user) == "primary-proof@example.com"
    await get_default_context().database.find_one_and_update(
        "object",
        {"id": identity.id},
        {"$set": {"context.email": "changed@example.com"}},
    )
    assert await verified_current_email(user) is None


async def test_unavailable_proof_does_not_block_actual_account_login(
    client, monkeypatch
):
    import uuid
    from unittest.mock import AsyncMock

    from app.services.permissions import get_user_node

    monkeypatch.setattr("app.services.email_verification.send_email", AsyncMock())
    email = f"nonblocking-{uuid.uuid4().hex}@example.com"
    created = await client.post(
        "/api/auth/signup",
        json={
            "email": email,
            "password": "testpassword123",
            "name": "Nonblocking proof",
        },
    )
    assert created.status_code == 200, created.text
    user = await get_user_node(created.json()["user"]["id"])
    assert user is not None
    await get_default_context().database.find_one_and_update(
        "node", {"id": user.id}, {"$set": {"context.email_verified": "true"}}
    )
    logged_in = await client.post(
        "/api/auth/login", json={"email": email, "password": "testpassword123"}
    )
    assert logged_in.status_code == 200, logged_in.text
    me = await client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer " + logged_in.json()["access_token"]},
    )
    assert me.status_code == 200, me.text
    data = me.json()["user"]
    assert data["email_verified"] is False
    assert data["email_verification_unavailable"] is True
    with pytest.raises(AuthorizationUnavailableError):
        await verified_current_email(user)
