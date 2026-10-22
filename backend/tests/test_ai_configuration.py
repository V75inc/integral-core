"""Private configuration ownership, legacy upgrade, and generation contracts."""

import base64
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.models.credentials import UserModelCredential
from app.schemas.ai_configuration import ConnectionWrite, ProfileWrite
from app.schemas.model_settings import ModelGenerationSettings
from app.services import ai_configuration as service
from app.services.credential_crypto import decrypt_secret_from_storage
from app.services.model_settings import provider_model_options

pytestmark = pytest.mark.smoke


@pytest.fixture(autouse=True)
def setup_keys(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "INTEGRAL_CREDENTIAL_ENC_KEY",
        base64.urlsafe_b64encode(b"c" * 32).decode().rstrip("="),
    )
    monkeypatch.setattr(service.settings, "INTEGRAL_AGENT_KEY_MODE", "hybrid")
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(
        service, "validate_provider_api_key", AsyncMock(return_value=(True, "verified"))
    )


@pytest.mark.asyncio
async def test_connection_secret_never_leaves_service_and_tests_saved_key():
    created = await service.save_connection(
        "owner",
        ConnectionWrite(
            name="Account", provider="openai", api_key="synthetic-provider-key"
        ),
    )
    assert "api_key_enc" not in created and "api_key" not in created
    assert "synthetic-provider-key" not in str(created)
    await service.test_connection("owner", created["id"])
    service.validate_provider_api_key.assert_awaited_with(
        "openai", "synthetic-provider-key", None
    )
    with pytest.raises(ValueError, match="unavailable"):
        await service.test_connection("other", created["id"])


@pytest.mark.asyncio
async def test_profiles_switch_without_key_reentry_or_speech_loss():
    await UserModelCredential(
        user_id="owner",
        provider="openai",
        model="previous",
        api_key_enc="",
        speech_model="",
    ).save()
    c = await service.save_connection(
        "owner",
        ConnectionWrite(
            name="Account", provider="openai", api_key="synthetic-provider-key"
        ),
    )
    a = await service.save_profile(
        "owner", ProfileWrite(name="Fast", connection_id=c["id"], model="gpt-4o-mini")
    )
    b = await service.save_profile(
        "owner", ProfileWrite(name="Other", connection_id=c["id"], model="gpt-4.1-mini")
    )
    await service.activate_profile("owner", a["id"])
    await service.activate_profile("owner", b["id"])
    record = await service.get_credential_for_user("owner")
    assert record.model == "gpt-4.1-mini"
    assert (
        decrypt_secret_from_storage(record.api_key_enc, aad="owner")
        == "synthetic-provider-key"
    )
    with pytest.raises(ValueError, match="another default"):
        await service.remove_item("owner", b["id"], connection=False)
    with pytest.raises(ValueError, match="profiles"):
        await service.remove_item("owner", c["id"], connection=True)
    with pytest.raises(ValueError, match="unavailable"):
        await service.save_profile(
            "other",
            ProfileWrite(name="Steal", connection_id=c["id"], model="gpt-4o-mini"),
        )


@pytest.mark.asyncio
async def test_legacy_snapshot_idempotent_and_key_rotation_updates_active_default(
    monkeypatch,
):
    from app.services import model_credentials

    monkeypatch.setattr(
        model_credentials,
        "validate_provider_api_key",
        AsyncMock(return_value=(True, "verified")),
    )
    legacy = await model_credentials.upsert_user_credential(
        user_id="owner",
        provider="openai",
        model="gpt-4o-mini",
        api_key="synthetic-original-key",
        speech_model="gpt-4o-mini-transcribe",
    )
    before = legacy.api_key_enc
    first = await service.configuration("owner")
    again = await service.configuration("owner")
    assert first == again
    assert (await service.get_credential_for_user("owner")).api_key_enc == before
    connection = first["connections"][0]
    await service.save_connection(
        "owner",
        ConnectionWrite(
            id=connection["id"],
            name="Rotated",
            provider="openai",
            api_key="synthetic-rotated-key",
        ),
    )
    current = await service.get_credential_for_user("owner")
    assert current.active_profile_id == first["default_profile_id"]
    assert (
        decrypt_secret_from_storage(current.api_key_enc, aad="owner")
        == "synthetic-rotated-key"
    )
    assert (
        decrypt_secret_from_storage(current.speech_api_key_enc, aad="owner")
        == "synthetic-original-key"
    )


@pytest.mark.asyncio
async def test_endpoint_rebind_and_platform_only_cannot_change_route(monkeypatch):
    c = await service.save_connection(
        "owner",
        ConnectionWrite(
            name="Local",
            provider="custom_openai",
            base_url="http://localhost:9000/v1",
            without_key=True,
        ),
    )
    with pytest.raises(ValueError, match="new connection"):
        await service.save_connection(
            "owner",
            ConnectionWrite(
                id=c["id"],
                name="Moved",
                provider="custom_openai",
                base_url="http://localhost:9001/v1",
                without_key=True,
            ),
        )
    monkeypatch.setattr(service.settings, "INTEGRAL_AGENT_KEY_MODE", "platform_only")
    with pytest.raises(ValueError, match="disabled"):
        await service.remove_item("owner", c["id"], connection=True)


@pytest.mark.asyncio
async def test_model_options_reach_selected_profile_and_do_not_change_default(
    monkeypatch,
):
    c = await service.save_connection(
        "owner",
        ConnectionWrite(
            name="Account", provider="openai", api_key="synthetic-provider-key"
        ),
    )
    p = await service.save_profile(
        "owner",
        ProfileWrite(
            name="Reason",
            connection_id=c["id"],
            model="gpt-5",
            generation_settings={"reasoning_effort": "high", "max_tokens": 2048},
        ),
    )
    await service.activate_profile("owner", p["id"])
    original = await service.get_credential_for_user("owner")
    fast = await service.save_profile(
        "owner", ProfileWrite(name="Fast", connection_id=c["id"], model="gpt-4o-mini")
    )
    selected = await service.profile_credential("owner", fast["id"], original)
    assert selected.model == "gpt-4o-mini"
    assert (await service.get_credential_for_user("owner")).model == "gpt-5"
    assert provider_model_options(original.generation_settings) == {
        "reasoning_effort": "high",
        "max_tokens": 2048,
    }
    from types import SimpleNamespace

    from app.services import model_credential_resolver as resolver

    monkeypatch.setattr(
        resolver, "get_workspace_owner_user_id", AsyncMock(return_value="owner-node")
    )
    monkeypatch.setattr(
        "app.services.permissions.get_user_node",
        AsyncMock(return_value=SimpleNamespace(user_id="owner")),
    )
    override = await resolver.resolve_native_model_override(
        "workspace", profile_id=fast["id"]
    )
    assert override["model"] == "openai/gpt-4o-mini"
    assert (await service.get_credential_for_user("owner")).model == "gpt-5"


@pytest.mark.parametrize(
    "value",
    [
        {"api_key": "forbidden"},
        {"temperature": 1, "top_p": 0.5},
        {"thinking_budget": 2048, "max_tokens": 2048},
        {"idle_timeout": 90, "request_timeout": 30},
    ],
)
def test_invalid_or_arbitrary_settings_rejected(value):
    with pytest.raises(ValidationError):
        ModelGenerationSettings.model_validate(value)


@pytest.mark.asyncio
async def test_public_api_lists_metadata_and_refuses_foreign_items(
    authenticated_client,
):
    c = await service.save_connection(
        "somebody-else",
        ConnectionWrite(
            name="Private", provider="openai", api_key="synthetic-other-key"
        ),
    )
    result = await authenticated_client.post(
        "/api/users/me/model-connections/test", json={"id": c["id"]}
    )
    assert result.status_code == 400
    assert "synthetic-other-key" not in result.text
    result = await authenticated_client.get("/api/users/me/ai-configuration")
    assert result.status_code == 200
    assert not result.json()["connections"]
    assert "effective" in result.json()


@pytest.mark.asyncio
async def test_api_saves_tests_and_activates_profile(authenticated_client):
    connection = await authenticated_client.post(
        "/api/users/me/model-connections",
        json={
            "name": "Account",
            "provider": "openai",
            "api_key": "synthetic-api-test-key",
        },
    )
    assert connection.status_code == 200, connection.text
    assert "synthetic-api-test-key" not in connection.text
    profile = await authenticated_client.post(
        "/api/users/me/model-profiles",
        json={
            "name": "Fast",
            "connection_id": connection.json()["id"],
            "model": "gpt-4o-mini",
        },
    )
    assert profile.status_code == 200, profile.text
    selected = await authenticated_client.post(
        "/api/users/me/model-profiles/activate", json={"id": profile.json()["id"]}
    )
    assert selected.status_code == 200, selected.text
    tested = await authenticated_client.post(
        "/api/users/me/model-connections/test", json={"id": connection.json()["id"]}
    )
    assert tested.status_code == 200 and tested.json()["valid"] is True


def test_harness_limits_are_validated_and_never_sent_to_provider(monkeypatch):
    from app.services import model_settings

    monkeypatch.setattr(model_settings, "model_capabilities", lambda *_: {})
    configured = {
        "model_request_limit": 2,
        "tool_call_limit": 4,
        "turn_token_limit": 4000,
        "turn_timeout": 60,
    }
    assert (
        model_settings.validate_model_settings("openai", "test", configured)
        == configured
    )
    assert provider_model_options(configured) == {}
    with pytest.raises(ValueError, match="ceiling"):
        model_settings.validate_model_settings(
            "openai", "test", {"model_request_limit": 100}
        )


@pytest.mark.asyncio
async def test_stale_connection_editor_cannot_restore_rotated_secret():
    from app.models.credentials import UserModelConnection

    created = await service.save_connection(
        "owner",
        ConnectionWrite(name="Account", provider="openai", api_key="synthetic-old-key"),
    )
    stale = (await UserModelConnection.get(created["id"])).model_copy()
    previous = stale.updated_at
    await service.save_connection(
        "owner",
        ConnectionWrite(
            id=created["id"],
            name="Account",
            provider="openai",
            api_key="synthetic-new-key",
        ),
    )
    stale.name = "Stale editor"
    with pytest.raises(ValueError, match="changed while editing"):
        await service._save_revision(stale, previous)
    current = await UserModelConnection.get(created["id"])
    assert (
        decrypt_secret_from_storage(current.api_key_enc, aad="owner")
        == "synthetic-new-key"
    )


@pytest.mark.asyncio
async def test_legacy_key_update_replaces_profile_selection(monkeypatch):
    from app.services import model_credentials

    monkeypatch.setattr(
        model_credentials,
        "validate_provider_api_key",
        AsyncMock(return_value=(True, "verified")),
    )
    c = await service.save_connection(
        "owner",
        ConnectionWrite(
            name="Account", provider="openai", api_key="synthetic-profile-key"
        ),
    )
    p = await service.save_profile(
        "owner",
        ProfileWrite(name="Default", connection_id=c["id"], model="gpt-4o-mini"),
    )
    await service.activate_profile("owner", p["id"])
    updated = await model_credentials.upsert_user_credential(
        user_id="owner",
        provider="openai",
        model="gpt-4o-mini",
        api_key="synthetic-legacy-key",
    )
    assert updated.active_profile_id == ""
    assert (
        decrypt_secret_from_storage(updated.api_key_enc, aad="owner")
        == "synthetic-legacy-key"
    )


@pytest.mark.asyncio
async def test_account_purge_removes_connections_and_profiles_only_for_owner():
    from app.services.model_credentials import delete_credentials_for_user

    own = await service.save_connection(
        "owner",
        ConnectionWrite(name="Own", provider="openai", api_key="synthetic-owner-key"),
    )
    other = await service.save_connection(
        "other",
        ConnectionWrite(name="Other", provider="openai", api_key="synthetic-other-key"),
    )
    await service.save_profile(
        "owner", ProfileWrite(name="Own", connection_id=own["id"], model="gpt-4o-mini")
    )
    assert await delete_credentials_for_user("owner") == 2
    snapshot = await service.configuration("owner")
    assert snapshot["connections"] == snapshot["profiles"] == []
    assert (await service.configuration("other"))["connections"][0]["id"] == other["id"]


@pytest.mark.asyncio
async def test_conversation_profile_select_reset_and_busy_guard(authenticated_client):
    from app.models.nodes import ChatThread

    await test_api_saves_tests_and_activates_profile(authenticated_client)
    config = (await authenticated_client.get("/api/users/me/ai-configuration")).json()
    profile_id = config["default_profile_id"]
    created = await authenticated_client.post(
        "/api/chat/threads",
        json={"provider_id": "integral_native", "title": "Profile QA"},
    )
    assert created.status_code in (200, 201), created.text
    thread_id = created.json()["id"]
    changed = await authenticated_client.post(
        "/api/users/me/conversation-model",
        json={"thread_id": thread_id, "profile_id": profile_id},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json() == {
        "profile_id": profile_id,
        "editable": True,
        "routing_mode": "profile",
    }
    reset = await authenticated_client.post(
        "/api/users/me/conversation-model",
        json={"thread_id": thread_id, "profile_id": ""},
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["profile_id"] == ""
    thread = await ChatThread.get(thread_id)
    thread.active_work_item_id = "busy-turn"
    await thread.save()
    busy = await authenticated_client.post(
        "/api/users/me/conversation-model",
        json={"thread_id": thread_id, "profile_id": profile_id},
    )
    assert busy.status_code == 400
    assert (await ChatThread.get(thread_id)).active_work_item_id == "busy-turn"


@pytest.mark.parametrize("reasoning", [False, True])
def test_capabilities_use_declared_efforts_and_gate_thinking(monkeypatch, reasoning):
    import litellm

    from app.services.model_settings import model_capabilities

    monkeypatch.setattr(
        litellm,
        "get_supported_openai_params",
        lambda **_: ["reasoning_effort", "thinking", "max_tokens"],
    )
    monkeypatch.setattr(
        litellm,
        "get_model_info",
        lambda **_: {
            "supports_reasoning": reasoning,
            "reasoning_effort_levels": ["high", "xhigh"],
        },
    )
    model_capabilities.cache_clear()
    try:
        caps = model_capabilities("anthropic", "synthetic-model")
        assert caps["reasoning_efforts"] == (["high", "xhigh"] if reasoning else [])
        assert caps["thinking_budget"] is reasoning
        assert (
            model_capabilities("custom_openai", "synthetic-model")["reasoning_efforts"]
            == []
        )
    finally:
        model_capabilities.cache_clear()


@pytest.mark.asyncio
async def test_strict_byok_does_not_advertise_deployment_fallback(
    authenticated_client, monkeypatch
):
    from app.config import settings

    monkeypatch.setattr(settings, "INTEGRAL_AGENT_KEY_MODE", "byo_strict")
    monkeypatch.setenv("INTEGRAL_NATIVE_MODEL", "deployment-only-model")
    result = await authenticated_client.get("/api/users/me/ai-configuration")
    assert result.status_code == 200
    assert result.json()["effective"]["source"] == "unavailable"
    assert result.json()["effective"]["model"] == ""


@pytest.mark.asyncio
async def test_saved_profile_replaces_warmed_lookup_before_next_dispatch():
    from app.models.credentials import UserModelProfile

    connection = await service.save_connection(
        "owner",
        ConnectionWrite(name="Account", provider="openai", api_key="synthetic-key"),
    )
    profile = await service.save_profile(
        "owner",
        ProfileWrite(name="Quick", connection_id=connection["id"], model="gpt-4o-mini"),
    )
    await UserModelProfile.get(profile["id"])
    await service.save_profile(
        "owner",
        ProfileWrite(
            id=profile["id"],
            name="Quick",
            connection_id=connection["id"],
            model="gpt-4.1",
            generation_settings={"max_tokens": 2048},
        ),
    )
    resolved = await service.profile_credential(
        "owner", profile["id"], UserModelCredential(user_id="owner")
    )
    assert resolved.model == "gpt-4.1"
    assert resolved.generation_settings == {"max_tokens": 2048}
    await service.activate_profile("owner", profile["id"])
    active = await service.get_credential_for_user("owner")
    assert active.model == "gpt-4.1"
    assert active.generation_settings == {"max_tokens": 2048}
