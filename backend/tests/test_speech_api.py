"""REST coverage for voice input: /agentive/speech/* and speech preferences."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import pytest

from app.agentive.services.speech.base import ClientSession, SttProviderError
from app.agentive.services.speech.providers.openai import OpenAISttProvider
from app.agentive.services.speech.rate_limit import reset_rate_limits
from app.config import settings
from app.models.credentials import UserModelCredential
from app.models.edges import IS_MEMBER_OF
from app.models.nodes import User, Workspace
from app.services.app_graph import catalog_user, catalog_workspace
from app.services.credential_crypto import encrypt_secret_for_storage
from app.services.model_credentials import (
    compute_key_fingerprint,
    delete_credentials_for_user,
)
from app.utils.time import utc_now_iso

OWNER_KEY = "sk-owner-key-1234"


@pytest.fixture
def speech_env(monkeypatch):
    """Encryption on, hybrid mode, no platform key, fresh rate limits."""
    key = base64.urlsafe_b64encode(b"7" * 32).decode().rstrip("=")
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", key)
    monkeypatch.setattr(settings, "INTEGRAL_AGENT_KEY_MODE", "hybrid")
    monkeypatch.setattr(settings, "SPEECH_ALLOW_PLATFORM_KEY", False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", None)
    reset_rate_limits()
    yield
    reset_rate_limits()


@pytest.fixture
def minted(monkeypatch):
    """Replace the vendor call; record the context it was handed."""
    calls = []

    async def fake_mint(self, ctx, opts):
        calls.append((ctx, opts))
        return ClientSession(
            engine="openai-realtime",
            transport="webrtc",
            connect_url="https://api.openai.com/v1/realtime/calls",
            client_secret="ek_test_secret",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
            params={"model": ctx.model},
        )

    monkeypatch.setattr(OpenAISttProvider, "mint_client_session", fake_mint)
    return calls


def _auth_user_id(user) -> str:
    return getattr(user, "user_id", None) or user.id


async def _give_owner_speech_slot(user) -> None:
    uid = _auth_user_id(user)
    await delete_credentials_for_user(uid)
    await UserModelCredential(
        user_id=uid,
        provider="openai",
        model="gpt-4.1",
        speech_model="gpt-live-transcribe",
        api_key_enc=encrypt_secret_for_storage(OWNER_KEY, aad=uid),
        key_fingerprint=compute_key_fingerprint(OWNER_KEY),
        is_active=True,
    ).save()


async def _org_where_caller_is_guest(test_user) -> Workspace:
    now = utc_now_iso()
    # The resolver keys the owner's credential on the auth-user id, so the
    # owner needs one (the fixture user from conftest already has it).
    owner = await User.create(
        user_id="speech-owner-auth-id",
        email="speech-owner@example.com",
        email_fold="speech-owner@example.com",
        created_at=now,
        updated_at=now,
    )
    await catalog_user(owner)
    ws = await Workspace.create(
        kind="organization",
        name="Speech Guest Org",
        name_fold="speech guest org",
        created_at=now,
        updated_at=now,
    )
    await catalog_workspace(ws)
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner", joined_at=now)
    await test_user.connect(ws, edge=IS_MEMBER_OF, role="guest", joined_at=now)
    await _give_owner_speech_slot(owner)
    return ws


# ---------------------------------------------------------------------------
# GET /agentive/speech/config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_config_without_provider_offers_browser_only(
    speech_env, authenticated_client, test_user
):
    """No speech slot: the browser recognizer is the only engine."""
    await delete_credentials_for_user(_auth_user_id(test_user))
    resp = await authenticated_client.get("/api/agentive/speech/config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [e["engine"] for e in body["engines"]] == ["webspeech"]
    assert body["engines"][0]["privacy_note"]
    assert body["provider"]["configured"] is False
    assert body["preferred_engine"] == "webspeech"
    assert body["preferences"]["hotkey"] == "Mod+Shift+Space"


@pytest.mark.asyncio
async def test_config_with_owner_slot_prefers_workspace_engine(
    speech_env, authenticated_client, test_user
):
    """The owner's speech slot puts the workspace engine first."""
    await _give_owner_speech_slot(test_user)
    resp = await authenticated_client.get("/api/agentive/speech/config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [e["engine"] for e in body["engines"]] == ["openai-realtime", "webspeech"]
    assert body["engines"][0]["source"] == "workspace"
    assert body["provider"] == {
        "configured": True,
        "available_to_you": True,
        "provider": "openai",
        "model": "gpt-live-transcribe",
        "source": "byok",
    }
    assert body["preferred_engine"] == "openai-realtime"
    assert OWNER_KEY not in resp.text


@pytest.mark.asyncio
async def test_config_honours_browser_preference(
    speech_env, authenticated_client, test_user
):
    """engine=browser keeps dictation on the browser even with a provider."""
    await _give_owner_speech_slot(test_user)
    patch = await authenticated_client.patch(
        "/api/users/me/speech-preferences", json={"engine": "browser"}
    )
    assert patch.status_code == 200, patch.text
    body = (await authenticated_client.get("/api/agentive/speech/config")).json()
    assert body["preferred_engine"] == "webspeech"


@pytest.mark.asyncio
async def test_config_for_guest_hides_workspace_engine(
    speech_env, authenticated_client, test_user
):
    """Guests see the provider exists but only get the browser engine."""
    ws = await _org_where_caller_is_guest(test_user)
    resp = await authenticated_client.get(
        "/api/agentive/speech/config", headers={"X-Integral-Scope": f"ws:{ws.id}"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["workspace_role"] == "guest"
    assert [e["engine"] for e in body["engines"]] == ["webspeech"]
    assert body["provider"]["configured"] is True
    assert body["provider"]["available_to_you"] is False


@pytest.mark.asyncio
async def test_config_rejects_malformed_scope_header(speech_env, authenticated_client):
    """A scope header not in ``ws:<id>`` form is a 400."""
    resp = await authenticated_client.get(
        "/api/agentive/speech/config", headers={"X-Integral-Scope": "not-a-scope"}
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_config_requires_auth(client):
    """Unauthenticated callers get 401."""
    resp = await client.get("/api/agentive/speech/config")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /agentive/speech/session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_mints_browser_credential_without_leaking_key(
    speech_env, minted, authenticated_client, test_user
):
    """The response carries the client secret, never the provider key."""
    await _give_owner_speech_slot(test_user)
    resp = await authenticated_client.post("/api/agentive/speech/session", json={})
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("cache-control") == "no-store"
    body = resp.json()
    assert body["client_secret"] == "ek_test_secret"
    assert body["transport"] == "webrtc"
    assert body["max_session_seconds"] == settings.SPEECH_MAX_SESSION_SECONDS
    assert OWNER_KEY not in resp.text

    ctx, opts = minted[0]
    assert ctx.api_key == OWNER_KEY
    assert ctx.model == "gpt-live-transcribe"
    assert ctx.safety_id and _auth_user_id(test_user) not in ctx.safety_id
    assert opts.ttl_seconds == settings.SPEECH_SESSION_TTL_SECONDS


@pytest.mark.asyncio
async def test_session_uses_request_language(
    speech_env, minted, authenticated_client, test_user
):
    """A request language overrides the stored preference."""
    await _give_owner_speech_slot(test_user)
    resp = await authenticated_client.post(
        "/api/agentive/speech/session", json={"language": "pt-BR"}
    )
    assert resp.status_code == 200, resp.text
    assert minted[0][1].language == "pt-BR"


@pytest.mark.asyncio
async def test_session_without_provider_is_409(
    speech_env, minted, authenticated_client, test_user
):
    """No speech slot and no platform key: 409 with a machine reason."""
    await delete_credentials_for_user(_auth_user_id(test_user))
    resp = await authenticated_client.post("/api/agentive/speech/session", json={})
    assert resp.status_code == 409, resp.text
    assert "speech_provider_not_configured" in resp.text
    assert minted == []


@pytest.mark.asyncio
async def test_session_for_guest_is_403(
    speech_env, minted, authenticated_client, test_user
):
    """Guests never mint against the workspace's provider."""
    ws = await _org_where_caller_is_guest(test_user)
    resp = await authenticated_client.post(
        "/api/agentive/speech/session",
        json={},
        headers={"X-Integral-Scope": f"ws:{ws.id}"},
    )
    assert resp.status_code == 403, resp.text
    assert minted == []


@pytest.mark.asyncio
async def test_session_is_rate_limited_per_user(
    speech_env, minted, authenticated_client, test_user, monkeypatch
):
    """Past the per-user limit the endpoint answers 429."""
    monkeypatch.setattr(settings, "SPEECH_SESSION_RATE_LIMIT", "2/60")
    await _give_owner_speech_slot(test_user)
    for _ in range(2):
        ok = await authenticated_client.post("/api/agentive/speech/session", json={})
        assert ok.status_code == 200, ok.text
    limited = await authenticated_client.post("/api/agentive/speech/session", json={})
    assert limited.status_code == 429, limited.text


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"language": "not a tag!"}, {"bogus": 1}, [1]])
async def test_session_rejects_invalid_body(
    speech_env, minted, authenticated_client, test_user, payload
):
    """Bad language tags, unknown fields and non-objects are 400s."""
    await _give_owner_speech_slot(test_user)
    resp = await authenticated_client.post("/api/agentive/speech/session", json=payload)
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_session_provider_rejection_is_503(
    speech_env, authenticated_client, test_user, monkeypatch
):
    """A vendor auth failure surfaces as 503 without the vendor message."""

    async def rejected(self, ctx, opts):
        raise SttProviderError("auth", "OpenAI rejected the API key")

    monkeypatch.setattr(OpenAISttProvider, "mint_client_session", rejected)
    await _give_owner_speech_slot(test_user)
    resp = await authenticated_client.post("/api/agentive/speech/session", json={})
    assert resp.status_code == 503, resp.text


@pytest.mark.asyncio
async def test_session_requires_auth(client):
    """Unauthenticated callers get 401."""
    resp = await client.post("/api/agentive/speech/session", json={})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /users/me/speech-preferences
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preferences_default_then_partial_update(
    speech_env, authenticated_client
):
    """GET returns defaults; PATCH merges one field and persists it."""
    resp = await authenticated_client.get("/api/users/me/speech-preferences")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "enabled": True,
        "engine": "auto",
        "language": "auto",
        "hotkey": "Mod+Shift+Space",
        "hotkey_mode": "hold_or_toggle",
        "auto_send_on_stop": False,
        "silence_timeout_seconds": 8,
    }

    patch = await authenticated_client.patch(
        "/api/users/me/speech-preferences",
        json={"auto_send_on_stop": True, "language": "en-GB"},
    )
    assert patch.status_code == 200, patch.text
    again = (await authenticated_client.get("/api/users/me/speech-preferences")).json()
    assert again["auto_send_on_stop"] is True
    assert again["language"] == "en-GB"
    assert again["hotkey"] == "Mod+Shift+Space"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"hotkey": "Space"},
        {"hotkey": "Mod+Mod+K"},
        {"language": "english please"},
        {"silence_timeout_seconds": 120},
        {"engine": "workspace"},
        {"unknown": 1},
    ],
)
async def test_preferences_reject_invalid_values(
    speech_env, authenticated_client, payload
):
    """Bare-key hotkeys, bad tags, out-of-range values and extras are 400s."""
    resp = await authenticated_client.patch(
        "/api/users/me/speech-preferences", json=payload
    )
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_preferences_require_auth(client):
    """Unauthenticated callers get 401."""
    resp = await client.get("/api/users/me/speech-preferences")
    assert resp.status_code == 401
