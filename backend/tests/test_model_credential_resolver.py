"""Tests for workspace-owner BYOK resolution."""

import base64

import pytest

from app.models.credentials import UserModelCredential
from app.services.credential_crypto import encrypt_secret_for_storage
from app.services.model_credential_resolver import (
    ModelKeyRequiredError,
    litellm_model_id,
    resolve_agent_model_override,
)
from app.services.model_credentials import compute_key_fingerprint
from app.services.personal_workspace import ensure_personal_workspace


@pytest.fixture
def enc_key(monkeypatch):
    """Provide a deterministic encryption key and hybrid mode for resolver tests."""
    key = base64.urlsafe_b64encode(b"1" * 32).decode().rstrip("=")
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", key)
    monkeypatch.setenv("INTEGRAL_AGENT_KEY_MODE", "hybrid")
    return key


@pytest.mark.asyncio
async def test_resolver_returns_none_without_owner_credential(enc_key, test_user):
    """Hybrid mode returns no override when the workspace owner has no credential."""
    workspace = await ensure_personal_workspace(test_user)
    override = await resolve_agent_model_override(workspace.id)
    assert override is None


@pytest.mark.asyncio
async def test_resolver_uses_owner_byok(enc_key, test_user):
    """Resolver returns the workspace owner's decrypted default-slot override."""
    workspace = await ensure_personal_workspace(test_user)
    workspace_id = workspace.id

    auth_user_id = getattr(test_user, "user_id", None) or test_user.id
    record = UserModelCredential(
        user_id=auth_user_id,
        provider="openai",
        model="gpt-4o-mini",
        api_key_enc=encrypt_secret_for_storage("sk-owner-key", aad=auth_user_id),
        key_fingerprint=compute_key_fingerprint("sk-owner-key"),
        is_active=True,
    )
    await record.save()

    override = await resolve_agent_model_override(workspace_id)
    assert override is not None
    assert override["slots"]["default"]["api_key"] == "sk-owner-key"
    # Provider routing remains explicit in the default model slot.
    assert override["slots"]["default"]["provider"] == "litellm"
    assert override["slots"]["default"]["model"] == "openai/gpt-4o-mini"
    assert override["slots"]["default"]["model"] == "openai/gpt-4o-mini"
    assert override["slots"]["default"]["provider"] == "litellm"
    assert "credential_ref" not in override

    bound = await resolve_agent_model_override(
        workspace_id, include_credential_identity=True
    )
    identity = bound["credential_ref"]
    assert identity.startswith("user-model-generation:")
    assert "sk-owner-key" not in identity
    assert record.api_key_enc not in identity
    # Telemetry writes performed by the resolver do not create a new key generation.
    repeated = await resolve_agent_model_override(
        workspace_id, include_credential_identity=True
    )
    assert repeated["credential_ref"] == identity
    record.api_key_enc = encrypt_secret_for_storage("sk-rotated-key", aad=auth_user_id)
    await record.save()
    rotated = await resolve_agent_model_override(
        workspace_id, include_credential_identity=True
    )
    assert rotated["credential_ref"] != identity
    assert rotated["slots"]["default"]["api_key"] == "sk-rotated-key"


@pytest.mark.asyncio
async def test_resolver_dual_provider_light_key(enc_key, test_user):
    """Resolver emits distinct default + light slots when providers differ."""
    workspace = await ensure_personal_workspace(test_user)
    auth_user_id = getattr(test_user, "user_id", None) or test_user.id
    record = UserModelCredential(
        user_id=auth_user_id,
        provider="openai",
        model="o3-mini",
        light_provider="anthropic",
        light_model="claude-3-5-haiku-latest",
        api_key_enc=encrypt_secret_for_storage("sk-openai-heavy", aad=auth_user_id),
        key_fingerprint=compute_key_fingerprint("sk-openai-heavy"),
        light_api_key_enc=encrypt_secret_for_storage("sk-ant-light", aad=auth_user_id),
        light_key_fingerprint=compute_key_fingerprint("sk-ant-light"),
        is_active=True,
    )
    await record.save()

    override = await resolve_agent_model_override(workspace.id)
    assert override is not None
    assert override["slots"]["default"]["provider"] == "litellm"
    assert override["slots"]["default"]["model"] == "openai/o3-mini"
    assert override["slots"]["default"]["api_key"] == "sk-openai-heavy"
    assert override["slots"]["light"]["provider"] == "litellm"
    assert override["slots"]["light"]["model"] == "anthropic/claude-3-5-haiku-latest"
    assert override["slots"]["light"]["api_key"] == "sk-ant-light"
    # Each slot carries its own provider inside the id, so a dual-provider
    # credential still routes through the one LiteLLM action.
    assert override["slots"]["light"]["provider"] == "litellm"
    assert override["slots"]["light"]["model"] == "anthropic/claude-3-5-haiku-latest"
    assert override["slots"]["default"]["api_key"] == "sk-openai-heavy"


@pytest.mark.asyncio
async def test_byo_strict_raises_without_credential(enc_key, test_user, monkeypatch):
    """byo_strict mode raises ModelKeyRequiredError when the owner has no key."""
    from app.services.model_credentials import delete_credentials_for_user

    monkeypatch.setattr(
        "app.services.model_credential_resolver.settings.INTEGRAL_AGENT_KEY_MODE",
        "byo_strict",
    )
    auth_user_id = getattr(test_user, "user_id", None) or test_user.id
    await delete_credentials_for_user(auth_user_id)
    workspace = await ensure_personal_workspace(test_user)
    with pytest.raises(ModelKeyRequiredError):
        await resolve_agent_model_override(workspace.id)


@pytest.mark.parametrize(
    ("provider", "model", "expected"),
    [
        # The live failure: a bare Ollama id reached LiteLLM as-is and drew
        # "LLM Provider NOT provided".
        ("ollama", "glm-5.3:cloud", "ollama/glm-5.3:cloud"),
        ("ollama_local", "gemma4:e2b", "ollama/gemma4:e2b"),
        ("openai", "gpt-4o-mini", "openai/gpt-4o-mini"),
        ("anthropic", "claude-sonnet-4-5", "anthropic/claude-sonnet-4-5"),
        # An OpenRouter id is itself vendor/model and still needs the route
        # prefix -- so "contains a slash" must not short-circuit composition.
        (
            "openrouter",
            "anthropic/claude-sonnet-4-5",
            "openrouter/anthropic/claude-sonnet-4-5",
        ),
        # Idempotent when the user already typed the prefix.
        ("openai", "openai/gpt-4o-mini", "openai/gpt-4o-mini"),
        ("openai", "OpenAI/gpt-4o-mini", "OpenAI/gpt-4o-mini"),
        # Unknown slug: pass through rather than invent a bad route.
        ("mystery", "some-model", "some-model"),
        ("openai", "", ""),
        ("", "gpt-4o-mini", "gpt-4o-mini"),
    ],
)
def test_litellm_model_id_composition(provider, model, expected):
    """Stored provider + bare model compose into a LiteLLM-routable id."""
    assert litellm_model_id(provider, model) == expected


@pytest.mark.asyncio
async def test_resolver_composes_ollama_id_for_litellm(enc_key, test_user):
    """An Ollama credential reaches the agent as ``ollama/<model>``.

    Regression: the bare id produced litellm.BadRequestError and tripped the
    orchestrator's model circuit breaker, so every turn fell back to the
    model-unavailable text.
    """
    workspace = await ensure_personal_workspace(test_user)
    auth_user_id = getattr(test_user, "user_id", None) or test_user.id
    record = UserModelCredential(
        user_id=auth_user_id,
        provider="ollama",
        model="glm-5.3:cloud",
        api_key_enc=encrypt_secret_for_storage("ollama-key", aad=auth_user_id),
        key_fingerprint=compute_key_fingerprint("ollama-key"),
        is_active=True,
    )
    await record.save()

    override = await resolve_agent_model_override(workspace.id)
    assert override is not None
    assert override["slots"]["default"]["model"] == "ollama/glm-5.3:cloud"
    assert override["slots"]["default"]["provider"] == "litellm"
    assert override["slots"]["default"]["model"] == "ollama/glm-5.3:cloud"


@pytest.mark.asyncio
async def test_resolver_omits_api_key_for_local_ollama(enc_key, test_user):
    """Local Ollama route must omit empty keys to avoid a blank Bearer header."""
    workspace = await ensure_personal_workspace(test_user)
    auth_user_id = getattr(test_user, "user_id", None) or test_user.id
    record = UserModelCredential(
        user_id=auth_user_id,
        provider="ollama_local",
        model="gemma4:e2b",
        is_active=True,
    )
    await record.save()

    override = await resolve_agent_model_override(workspace.id)
    assert override is not None
    assert override["slots"]["default"]["provider"] == "litellm"
    assert override["slots"]["default"]["model"] == "ollama/gemma4:e2b"
    assert "api_key" not in override
    assert override["slots"]["default"] == {
        "provider": "litellm",
        "model": "ollama/gemma4:e2b",
    }


@pytest.mark.asyncio
async def test_key_source_local_ollama_is_byok(enc_key, test_user):
    """Keyless ollama_local must not be misclassified as platform-funded."""
    from app.services.model_credential_resolver import resolve_agent_key_source

    workspace = await ensure_personal_workspace(test_user)
    auth_user_id = getattr(test_user, "user_id", None) or test_user.id
    record = UserModelCredential(
        user_id=auth_user_id,
        provider="ollama_local",
        model="gemma4:e2b",
        is_active=True,
    )
    await record.save()

    assert await resolve_agent_key_source(workspace.id) == "byok"


@pytest.mark.asyncio
async def test_key_source_encrypted_byok(enc_key, test_user):
    from app.services.model_credential_resolver import resolve_agent_key_source

    workspace = await ensure_personal_workspace(test_user)
    auth_user_id = getattr(test_user, "user_id", None) or test_user.id
    record = UserModelCredential(
        user_id=auth_user_id,
        provider="openai",
        model="gpt-4o-mini",
        api_key_enc=encrypt_secret_for_storage("sk-owner-key", aad=auth_user_id),
        key_fingerprint=compute_key_fingerprint("sk-owner-key"),
        is_active=True,
    )
    await record.save()

    assert await resolve_agent_key_source(workspace.id) == "byok"


@pytest.mark.asyncio
async def test_key_source_missing_credential_is_platform(enc_key, test_user):
    from app.services.model_credential_resolver import resolve_agent_key_source

    workspace = await ensure_personal_workspace(test_user)
    assert await resolve_agent_key_source(workspace.id) == "platform"


@pytest.mark.asyncio
async def test_key_source_platform_only_mode(enc_key, test_user, monkeypatch):
    from app.services.model_credential_resolver import resolve_agent_key_source

    monkeypatch.setenv("INTEGRAL_AGENT_KEY_MODE", "platform_only")
    from app.config import settings

    monkeypatch.setattr(settings, "INTEGRAL_AGENT_KEY_MODE", "platform_only")
    workspace = await ensure_personal_workspace(test_user)
    auth_user_id = getattr(test_user, "user_id", None) or test_user.id
    record = UserModelCredential(
        user_id=auth_user_id,
        provider="ollama_local",
        model="gemma4:e2b",
        is_active=True,
    )
    await record.save()
    assert await resolve_agent_key_source(workspace.id) == "platform"


@pytest.mark.asyncio
async def test_key_source_byo_strict_reports_byok_without_credential(
    enc_key, test_user, monkeypatch
):
    from app.services.model_credential_resolver import resolve_agent_key_source

    monkeypatch.setattr(
        __import__("app.config", fromlist=["settings"]).settings,
        "INTEGRAL_AGENT_KEY_MODE",
        "byo_strict",
    )
    workspace = await ensure_personal_workspace(test_user)
    assert await resolve_agent_key_source(workspace.id) == "byok"
