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
async def test_post_saves_local_ollama_without_api_key(enc_key, authenticated_client):
    """Local provider setup is selectable without collecting a hosted key."""
    with patch(
        "app.services.model_credentials.validate_provider_api_key",
        new=AsyncMock(return_value=(True, "local Ollama reachable")),
    ) as validate:
        resp = await authenticated_client.post(
            "/api/users/me/model-credentials",
            json={"provider": "ollama_local", "model": "gemma4:e2b"},
        )

    assert resp.status_code == 200
    assert resp.json()["credential"]["provider"] == "ollama_local"
    assert resp.json()["credential"]["key_fingerprint"] == ""
    validate.assert_awaited_once_with("ollama_local", "")


@pytest.mark.asyncio
@pytest.mark.parametrize("slot", ["light", "heavy", "vision"])
async def test_retired_model_slot_is_rejected(enc_key, authenticated_client, slot):
    resp = await authenticated_client.post(
        "/api/users/me/model-credentials",
        json={
            "provider": "openai",
            "model": "gpt-4.1",
            "api_key": "sk-live-test-key-1234",
            f"{slot}_model": "retired-model",
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_post_update_primary_model_without_resubmitting_api_key(
    enc_key, authenticated_client, test_user
):
    """POST can update the primary model while retaining its encrypted key."""
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
                "model": "gpt-4.1-mini",
            },
        )
    assert resp.status_code == 200
    body = resp.json()["credential"]
    assert body["model"] == "gpt-4.1-mini"
    assert body["key_fingerprint"]


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


@pytest.mark.asyncio
async def test_retired_key_rejection_does_not_expose_secret(
    enc_key, authenticated_client, caplog
):
    secret = "synthetic-retired-slot-secret-do-not-log"
    response = await authenticated_client.post(
        "/api/users/me/model-credentials",
        json={
            "provider": "openai",
            "model": "gpt-4.1",
            "api_key": "sk-synthetic-primary-key",
            "light_api_key": secret,
        },
    )
    assert response.status_code == 400
    assert "light_api_key" in response.text
    assert secret not in response.text
    assert secret not in caplog.text
