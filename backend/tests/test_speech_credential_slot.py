"""Voice input (speech) slot on the BYOK model credential, and its resolver."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.models.credentials import UserModelCredential
from app.services.credential_crypto import encrypt_secret_for_storage
from app.services.model_credential_resolver import resolve_speech_credential
from app.services.model_credentials import (
    compute_key_fingerprint,
    decrypt_credential_speech_api_key,
    delete_credentials_for_user,
    upsert_user_credential,
)
from app.services.personal_workspace import ensure_personal_workspace

_VALID = "app.services.model_credentials.validate_provider_api_key"


@pytest.fixture
def enc_key(monkeypatch):
    """Deterministic encryption key, hybrid mode, platform speech key off."""
    key = base64.urlsafe_b64encode(b"5" * 32).decode().rstrip("=")
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", key)
    monkeypatch.setattr(settings, "INTEGRAL_AGENT_KEY_MODE", "hybrid")
    monkeypatch.setattr(settings, "SPEECH_ALLOW_PLATFORM_KEY", False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", None)
    return key


def _auth_user_id(test_user) -> str:
    return getattr(test_user, "user_id", None) or test_user.id


def _keys_ok():
    return patch(_VALID, new=AsyncMock(return_value=(True, "validated")))


# ---------------------------------------------------------------------------
# Service: upsert / decrypt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_speech_slot_same_provider_reuses_primary_key(enc_key, test_user):
    """Same provider as the default slot: stored as inherit, no own key."""
    uid = _auth_user_id(test_user)
    with _keys_ok():
        record = await upsert_user_credential(
            user_id=uid,
            provider="openai",
            model="gpt-4.1",
            api_key="sk-primary-key-1234",
            speech_model="gpt-live-transcribe",
        )

    assert record.speech_model == "gpt-live-transcribe"
    assert record.speech_provider == ""
    assert record.speech_api_key_enc == ""
    assert decrypt_credential_speech_api_key(record) == "sk-primary-key-1234"


@pytest.mark.asyncio
async def test_speech_slot_separate_provider_stores_own_key(enc_key, test_user):
    """A different speech provider stores its own encrypted key."""
    uid = _auth_user_id(test_user)
    with _keys_ok():
        record = await upsert_user_credential(
            user_id=uid,
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            api_key="sk-ant-primary-1234",
            speech_model="gpt-live-transcribe",
            speech_provider="openai",
            speech_api_key="sk-openai-speech-5678",
        )

    assert record.speech_provider == "openai"
    assert record.speech_api_key_enc.startswith("v1:")
    assert "sk-openai-speech-5678" not in record.speech_api_key_enc
    assert record.speech_key_fingerprint == compute_key_fingerprint(
        "sk-openai-speech-5678"
    )
    assert decrypt_credential_speech_api_key(record) == "sk-openai-speech-5678"


@pytest.mark.asyncio
async def test_speech_slot_requires_key_when_provider_differs(enc_key, test_user):
    """A separate speech provider without its own key is rejected."""
    uid = _auth_user_id(test_user)
    with _keys_ok(), pytest.raises(ValueError, match="speech_api_key is required"):
        await upsert_user_credential(
            user_id=uid,
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            api_key="sk-ant-primary-1234",
            speech_model="gpt-live-transcribe",
            speech_provider="openai",
        )


@pytest.mark.asyncio
async def test_speech_slot_rejects_provider_without_stt(enc_key, test_user):
    """Anthropic has no speech-to-text API, so it cannot back voice input."""
    uid = _auth_user_id(test_user)
    with _keys_ok(), pytest.raises(ValueError, match="voice input provider"):
        await upsert_user_credential(
            user_id=uid,
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            api_key="sk-ant-primary-1234",
            speech_model="some-model",
        )


@pytest.mark.asyncio
async def test_turning_speech_off_drops_its_key(enc_key, test_user):
    """An empty speech model clears the slot, including its stored key."""
    uid = _auth_user_id(test_user)
    with _keys_ok():
        await upsert_user_credential(
            user_id=uid,
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            api_key="sk-ant-primary-1234",
            speech_model="gpt-live-transcribe",
            speech_provider="openai",
            speech_api_key="sk-openai-speech-5678",
        )
        record = await upsert_user_credential(
            user_id=uid,
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            speech_model="",
        )

    assert record.speech_model == ""
    assert record.speech_provider == ""
    assert record.speech_api_key_enc == ""
    assert record.speech_key_fingerprint == ""


# ---------------------------------------------------------------------------
# API: /users/me/model-credentials carries the speech slot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_round_trips_speech_slot_without_leaking_key(
    enc_key, authenticated_client
):
    """POST then GET returns speech metadata and fingerprint, never the key."""
    with _keys_ok():
        resp = await authenticated_client.post(
            "/api/users/me/model-credentials",
            json={
                "provider": "anthropic",
                "model": "claude-sonnet-4-20250514",
                "api_key": "sk-ant-primary-1234",
                "speech_model": "gpt-live-transcribe",
                "speech_provider": "openai",
                "speech_api_key": "sk-openai-speech-5678",
            },
        )
    assert resp.status_code == 200, resp.text
    assert "sk-openai-speech-5678" not in resp.text

    get_resp = await authenticated_client.get("/api/users/me/model-credentials")
    assert get_resp.status_code == 200
    assert "sk-openai-speech-5678" not in get_resp.text
    loaded = get_resp.json()["credential"]
    assert loaded["speech_model"] == "gpt-live-transcribe"
    assert loaded["speech_provider"] == "openai"
    assert loaded["speech_key_fingerprint"] == compute_key_fingerprint(
        "sk-openai-speech-5678"
    )


@pytest.mark.asyncio
async def test_api_rejects_speech_slot_on_provider_without_stt(
    enc_key, authenticated_client
):
    """A speech model on a non-STT primary with no speech provider is a 400."""
    with _keys_ok():
        resp = await authenticated_client.post(
            "/api/users/me/model-credentials",
            json={
                "provider": "anthropic",
                "model": "claude-sonnet-4-20250514",
                "api_key": "sk-ant-primary-1234",
                "speech_model": "gpt-live-transcribe",
            },
        )
    assert resp.status_code == 400
    assert "speech-to-text provider" in resp.text


@pytest.mark.asyncio
async def test_api_requires_auth(client):
    """The credential surface is authenticated."""
    resp = await client.get("/api/users/me/model-credentials")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Resolver: key-mode matrix
# ---------------------------------------------------------------------------


async def _save_owner_credential(test_user, *, speech_model: str = "") -> None:
    uid = _auth_user_id(test_user)
    await delete_credentials_for_user(uid)
    await UserModelCredential(
        user_id=uid,
        provider="openai",
        model="gpt-4.1",
        speech_model=speech_model,
        api_key_enc=encrypt_secret_for_storage("sk-owner-key-1234", aad=uid),
        key_fingerprint=compute_key_fingerprint("sk-owner-key-1234"),
        is_active=True,
    ).save()


def _allow_platform(monkeypatch, allowed: bool) -> None:
    monkeypatch.setattr(settings, "SPEECH_ALLOW_PLATFORM_KEY", allowed)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-platform-key-9999")


@pytest.mark.asyncio
async def test_hybrid_uses_owner_speech_slot(enc_key, test_user, monkeypatch):
    """Hybrid: the owner's speech slot wins over an allowed platform key."""
    _allow_platform(monkeypatch, True)
    await _save_owner_credential(test_user, speech_model="gpt-live-transcribe")
    workspace = await ensure_personal_workspace(test_user)

    cred = await resolve_speech_credential(workspace.id)

    assert cred is not None
    assert cred.source == "byok"
    assert cred.provider == "openai"
    assert cred.model == "gpt-live-transcribe"
    assert cred.api_key == "sk-owner-key-1234"


@pytest.mark.asyncio
async def test_hybrid_without_slot_and_platform_disallowed_is_none(enc_key, test_user):
    """Hybrid with no slot and no platform opt-in resolves to nothing."""
    await _save_owner_credential(test_user, speech_model="")
    workspace = await ensure_personal_workspace(test_user)

    assert await resolve_speech_credential(workspace.id) is None


@pytest.mark.asyncio
async def test_hybrid_without_slot_falls_back_to_platform_when_allowed(
    enc_key, test_user, monkeypatch
):
    """Hybrid with no slot uses the platform key once the operator opts in."""
    _allow_platform(monkeypatch, True)
    await _save_owner_credential(test_user, speech_model="")
    workspace = await ensure_personal_workspace(test_user)

    cred = await resolve_speech_credential(workspace.id)

    assert cred is not None
    assert cred.source == "platform"
    assert cred.api_key == "sk-platform-key-9999"
    assert cred.model == settings.SPEECH_STREAM_MODEL_DEFAULT


@pytest.mark.asyncio
async def test_byo_strict_never_uses_platform_key(enc_key, test_user, monkeypatch):
    """byo_strict ignores the platform key even when allowed."""
    _allow_platform(monkeypatch, True)
    monkeypatch.setattr(settings, "INTEGRAL_AGENT_KEY_MODE", "byo_strict")
    await _save_owner_credential(test_user, speech_model="")
    workspace = await ensure_personal_workspace(test_user)

    assert await resolve_speech_credential(workspace.id) is None


@pytest.mark.asyncio
async def test_platform_only_ignores_owner_slot(enc_key, test_user, monkeypatch):
    """platform_only uses the platform key even when the owner has a slot."""
    _allow_platform(monkeypatch, True)
    monkeypatch.setattr(settings, "INTEGRAL_AGENT_KEY_MODE", "platform_only")
    await _save_owner_credential(test_user, speech_model="gpt-live-transcribe")
    workspace = await ensure_personal_workspace(test_user)

    cred = await resolve_speech_credential(workspace.id)

    assert cred is not None
    assert cred.source == "platform"


@pytest.mark.asyncio
async def test_platform_only_without_opt_in_is_none(enc_key, test_user, monkeypatch):
    """platform_only without the platform opt-in resolves to nothing."""
    _allow_platform(monkeypatch, False)
    monkeypatch.setattr(settings, "INTEGRAL_AGENT_KEY_MODE", "platform_only")
    workspace = await ensure_personal_workspace(test_user)

    assert await resolve_speech_credential(workspace.id) is None


@pytest.mark.asyncio
async def test_speech_credential_repr_hides_key(enc_key, test_user):
    """The resolved credential never prints its key."""
    await _save_owner_credential(test_user, speech_model="gpt-live-transcribe")
    workspace = await ensure_personal_workspace(test_user)

    cred = await resolve_speech_credential(workspace.id)

    assert cred is not None
    assert "sk-owner-key-1234" not in repr(cred)
