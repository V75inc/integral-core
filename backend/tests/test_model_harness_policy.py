"""Role policy is evidence-labelled and enforced without provider fallback."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.model_harness_policy import (
    assess_model,
    model_policy_catalog,
    require_heavy_model,
)

pytestmark = pytest.mark.smoke


@pytest.mark.parametrize(
    "provider,model",
    [
        ("openai", "gpt-5.4-mini"),
        ("openai", "o3-mini"),
        ("openrouter", "openai/o4-mini-2025-04-16"),
        ("openai", "gpt-4.1-nano-2025-04-14"),
        ("openai", "openai/gpt-4o-mini"),
        ("openrouter", "openai/gpt-5.4-mini"),
        ("openrouter", "anthropic/claude-3.5-haiku"),
        ("anthropic", "claude-haiku-4-5-20251001"),
        ("ollama_local", "gemma4:e2b"),
        ("custom_openai", "gpt-5.4-mini"),
    ],
)
def test_light_models_are_never_heavy(provider, model):
    assert assess_model(provider, model)["tier"] == "light"
    with pytest.raises(ValueError, match="light model"):
        require_heavy_model(provider, model)


def test_evidence_and_provider_boundaries():
    assert assess_model("openai", "gpt-6.1-sol")["evidence"] == "observed"
    assert assess_model("openrouter", "openai/gpt-6.1-sol")["evidence"] == "candidate"
    assert assess_model("custom_openai", "gpt-6.1-sol")["tier"] == "unverified"
    assert assess_model("openai", "gpt-4o-mini-transcribe")["tier"] == "unverified"
    require_heavy_model("custom_openai", "my-evaluated-model")
    catalog = model_policy_catalog()
    catalog["providers"]["openai"]["preferred_heavy"] = "changed"
    assert (
        model_policy_catalog()["providers"]["openai"]["preferred_heavy"]
        == "gpt-6.1-sol"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["platform", "workspace_byok", "local"])
async def test_runtime_legacy_or_fixed_heavy_cannot_bypass_policy(source):
    from app.agentive.harness.model_routing import _resolve_turn_routes

    resolver = AsyncMock(
        return_value=SimpleNamespace(
            provider="openai",
            model="openai/gpt-5.4-mini",
            credential_source=source,
        )
    )
    with pytest.raises(ValueError, match="light model"):
        await _resolve_turn_routes(
            workspace_id="workspace",
            profile_id="fixed",
            resolver=resolver,
            heavy_only=False,
        )


@pytest.mark.asyncio
async def test_legacy_write_rejected_before_credentials_are_read(monkeypatch):
    from app.services import model_credentials

    monkeypatch.setattr(model_credentials.settings, "INTEGRAL_AGENT_KEY_MODE", "hybrid")
    read = AsyncMock()
    monkeypatch.setattr(model_credentials, "get_credential_for_user", read)
    with pytest.raises(ValueError, match="light model"):
        await model_credentials.upsert_user_credential(
            user_id="owner", provider="openai", model="gpt-5.4-mini"
        )
    read.assert_not_awaited()


@pytest.mark.asyncio
async def test_activation_rejects_light_before_key_access(monkeypatch):
    from app.services import ai_configuration as service

    monkeypatch.setattr(service, "_enabled", lambda: None)
    monkeypatch.setattr(
        service,
        "_owned",
        AsyncMock(
            side_effect=[
                SimpleNamespace(connection_id="connection", model="gpt-5.4-mini"),
                SimpleNamespace(provider="openai"),
            ]
        ),
    )
    save = AsyncMock()
    monkeypatch.setattr(service, "_save_revision", save)
    with pytest.raises(ValueError, match="light model"):
        await service.activate_profile("owner", "profile")
    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_active_heavy_edit_rejected_before_profile_mutation(monkeypatch):
    from app.schemas.ai_configuration import ProfileWrite
    from app.services import ai_configuration as service

    profile = SimpleNamespace(id="profile", model="gpt-6.1-sol")
    monkeypatch.setattr(service, "_enabled", lambda: None)
    monkeypatch.setattr(
        service,
        "_owned",
        AsyncMock(
            side_effect=[
                SimpleNamespace(id="connection", provider="openai"),
                profile,
            ]
        ),
    )
    monkeypatch.setattr(service, "validate_model_settings", lambda *args: {})
    monkeypatch.setattr(
        service,
        "get_credential_for_user",
        AsyncMock(
            return_value=SimpleNamespace(is_active=True, active_profile_id="profile")
        ),
    )
    save = AsyncMock()
    monkeypatch.setattr(service, "_save_revision", save)
    with pytest.raises(ValueError, match="light model"):
        await service.save_profile(
            "owner",
            ProfileWrite(
                id="profile",
                name="Default",
                connection_id="connection",
                model="gpt-5.4-mini",
            ),
        )
    assert profile.model == "gpt-6.1-sol"
    save.assert_not_awaited()
