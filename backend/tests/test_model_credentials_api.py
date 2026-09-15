"""API tests for /users/me/model-credentials."""

import base64
from unittest.mock import AsyncMock, patch

import pytest

from app.models.credentials import UserModelCredential


@pytest.fixture
def enc_key(monkeypatch):
    """Provide a deterministic encryption key and hybrid mode for API tests."""
    key = base64.urlsafe_b64encode(b"2" * 32).decode().rstrip("=")
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", key)
    monkeypatch.setenv("INTEGRAL_AGENT_KEY_MODE", "hybrid")
    return key


@pytest.mark.asyncio
async def test_get_returns_metadata_only(enc_key, authenticated_client, test_user):
    """GET returns provider + fingerprint metadata, never the plaintext key."""
    from app.services.model_credentials import (
        compute_key_fingerprint,
        upsert_user_credential,
    )

    auth_user_id = getattr(test_user, "user_id", None) or test_user.id
    with patch(
        "app.services.model_credentials.validate_provider_api_key",
        new=AsyncMock(return_value=(True, "validated")),
    ):
        await upsert_user_credential(
            user_id=auth_user_id,
            provider="openai",
            model="gpt-4o-mini",
            api_key="sk-live-test-key-1234",
        )

    resp = await authenticated_client.get("/api/users/me/model-credentials")
    assert resp.status_code == 200
    body = resp.json()
    assert body["credential"]["provider"] == "openai"
    assert body["credential"]["key_fingerprint"] == compute_key_fingerprint(
        "sk-live-test-key-1234"
    )


@pytest.mark.asyncio
async def test_post_validates_before_save(enc_key, authenticated_client):
    """POST with an invalid key is rejected (400) before any persistence."""
    with patch(
        "app.services.model_credentials.validate_provider_api_key",
        new=AsyncMock(return_value=(False, "invalid API key")),
    ):
        resp = await authenticated_client.post(
            "/api/users/me/model-credentials",
            json={
                "provider": "openai",
                "model": "gpt-4o-mini",
                "api_key": "sk-bad",
            },
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_post_persists_heavy_and_vision_slots(
    enc_key, authenticated_client, test_user
):
    """POST persists heavy + vision slot models, readable back via GET."""
    with patch(
        "app.services.model_credentials.validate_provider_api_key",
        new=AsyncMock(return_value=(True, "validated")),
    ):
        resp = await authenticated_client.post(
            "/api/users/me/model-credentials",
            json={
                "provider": "openai",
                "model": "gpt-4.1",
                "api_key": "sk-live-test-key-1234",
                "light_model": "gpt-4o-mini",
                "heavy_model": "o3-mini",
                "vision_model": "gpt-4o",
            },
        )
    assert resp.status_code == 200
    body = resp.json()["credential"]
    assert body["heavy_model"] == "o3-mini"
    assert body["vision_model"] == "gpt-4o"

    get_resp = await authenticated_client.get("/api/users/me/model-credentials")
    assert get_resp.status_code == 200
    loaded = get_resp.json()["credential"]
    assert loaded["heavy_model"] == "o3-mini"
    assert loaded["vision_model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_post_update_slots_without_resubmitting_api_key(
    enc_key, authenticated_client, test_user
):
    """POST can add slots without resubmitting the existing default API key."""
    from app.services.model_credentials import upsert_user_credential

    auth_user_id = getattr(test_user, "user_id", None) or test_user.id
    with patch(
        "app.services.model_credentials.validate_provider_api_key",
        new=AsyncMock(return_value=(True, "validated")),
    ):
        await upsert_user_credential(
            user_id=auth_user_id,
            provider="openai",
            model="gpt-4.1",
            api_key="sk-live-test-key-1234",
        )

    with patch(
        "app.services.model_credentials.validate_provider_api_key",
        new=AsyncMock(return_value=(True, "validated")),
    ):
        resp = await authenticated_client.post(
            "/api/users/me/model-credentials",
            json={
                "provider": "openai",
                "model": "gpt-4.1",
                "heavy_model": "o3-mini",
                "vision_model": "gpt-4o",
            },
        )
    assert resp.status_code == 200
    body = resp.json()["credential"]
    assert body["heavy_model"] == "o3-mini"
    assert body["vision_model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_delete_revokes(enc_key, authenticated_client, test_user):
    """DELETE deactivates the active credential (no active rows remain)."""
    from app.services.model_credentials import upsert_user_credential

    auth_user_id = getattr(test_user, "user_id", None) or test_user.id
    with patch(
        "app.services.model_credentials.validate_provider_api_key",
        new=AsyncMock(return_value=(True, "validated")),
    ):
        await upsert_user_credential(
            user_id=auth_user_id,
            provider="anthropic",
            model="claude-3-5-sonnet-latest",
            api_key="sk-ant-test-key-9999",
        )

    resp = await authenticated_client.delete("/api/users/me/model-credentials")
    assert resp.status_code == 200
    rows = await UserModelCredential.find(
        {"context.user_id": auth_user_id, "context.is_active": True}
    )
    assert rows == []


@pytest.mark.asyncio
async def test_account_deletion_purges_credentials(enc_key, test_user):
    """Account-deletion purge hard-removes all credential rows for the user."""
    from app.services.model_credentials import (
        delete_credentials_for_user,
        get_active_credential_for_user,
        upsert_user_credential,
    )

    auth_user_id = getattr(test_user, "user_id", None) or test_user.id
    with patch(
        "app.services.model_credentials.validate_provider_api_key",
        new=AsyncMock(return_value=(True, "validated")),
    ):
        await upsert_user_credential(
            user_id=auth_user_id,
            provider="openai",
            model="gpt-4o-mini",
            api_key="sk-delete-test-key",
        )
    assert await get_active_credential_for_user(auth_user_id) is not None
    removed = await delete_credentials_for_user(auth_user_id)
    assert removed >= 1
    assert await get_active_credential_for_user(auth_user_id) is None
