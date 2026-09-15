"""Service-layer tests for UserModelCredential upsert/dedupe."""

import base64
from unittest.mock import AsyncMock, patch

import pytest

from app.models.credentials import UserModelCredential
from app.services.model_credentials import (
    dedupe_user_model_credentials,
    get_credential_for_user,
    revoke_user_credential,
    upsert_user_credential,
)


@pytest.fixture
def enc_key(monkeypatch):
    key = base64.urlsafe_b64encode(b"3" * 32).decode().rstrip("=")
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", key)
    monkeypatch.setenv("INTEGRAL_AGENT_KEY_MODE", "hybrid")
    return key


@pytest.mark.asyncio
async def test_upsert_after_revoke_reuses_same_row(enc_key, test_user):
    """Revoke then save again must not create a second row for the same user."""
    auth_user_id = getattr(test_user, "user_id", None) or test_user.id
    with patch(
        "app.services.model_credentials.validate_provider_api_key",
        new=AsyncMock(return_value=(True, "validated")),
    ):
        first = await upsert_user_credential(
            user_id=auth_user_id,
            provider="openai",
            model="gpt-4o-mini",
            api_key="sk-live-test-key-1234",
        )
        await revoke_user_credential(auth_user_id)
        second = await upsert_user_credential(
            user_id=auth_user_id,
            provider="openai",
            model="gpt-4.1",
            api_key="sk-live-test-key-5678",
        )

    assert second.id == first.id
    assert second.is_active is True
    assert second.model == "gpt-4.1"
    rows = await UserModelCredential.find({"context.user_id": auth_user_id})
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_dedupe_user_model_credentials_collapses_duplicates(enc_key, test_user):
    """Startup dedupe keeps one canonical row per user_id."""
    auth_user_id = getattr(test_user, "user_id", None) or test_user.id
    inactive = UserModelCredential(
        user_id=auth_user_id,
        provider="openai",
        model="old",
        is_active=False,
        created_at="2020-01-01T00:00:00Z",
    )
    active = UserModelCredential(
        user_id=auth_user_id,
        provider="anthropic",
        model="new",
        is_active=True,
        created_at="2021-01-01T00:00:00Z",
    )
    await inactive.save()
    await active.save()

    removed = await dedupe_user_model_credentials()
    assert removed == 1

    remaining = await UserModelCredential.find({"context.user_id": auth_user_id})
    assert len(remaining) == 1
    canonical = await get_credential_for_user(auth_user_id)
    assert canonical is not None
    assert canonical.id == active.id
    assert canonical.is_active is True
