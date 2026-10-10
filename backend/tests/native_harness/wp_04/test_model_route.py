"""Trusted credential resolver to native route contract tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.agentive.harness.model_route import resolve_native_model_route
from app.api.errors import ServiceUnavailableError


@pytest.fixture(autouse=True)
def isolated_deployment_identity(monkeypatch):
    monkeypatch.delenv("INTEGRAL_NATIVE_CREDENTIAL_REF", raising=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model,source",
    [("openai/gpt-4.1", "platform"), ("ollama/deepseek-v4.1-flash:cloud", "local")],
)
async def test_deployment_generation_reference_is_optional_and_versioned(
    monkeypatch, model, source
):
    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_native_model_override",
        AsyncMock(return_value=None),
    )
    route = await resolve_native_model_route(
        workspace_id="workspace-1", default_model=model
    )
    assert route.credential_ref is None
    monkeypatch.setenv("INTEGRAL_NATIVE_CREDENTIAL_REF", "deployment-generation:v1")
    first = await resolve_native_model_route(
        workspace_id="workspace-1", default_model=model
    )
    if source == "platform":
        assert first.credential_ref == "deployment-generation:v1"
    else:
        assert first.credential_ref.startswith("local-model-generation:")
    assert first.credential_source == source
    assert first.api_key is None
    monkeypatch.setenv("INTEGRAL_NATIVE_CREDENTIAL_REF", "deployment-generation:v2")
    second = await resolve_native_model_route(
        workspace_id="workspace-1", default_model=model
    )
    assert second.credential_ref != first.credential_ref


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", [" padded", "padded ", " ", "x" * 256])
async def test_invalid_deployment_generation_fails_closed(monkeypatch, reference):
    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_native_model_override",
        AsyncMock(return_value=None),
    )
    monkeypatch.setenv("INTEGRAL_NATIVE_CREDENTIAL_REF", reference)
    with pytest.raises(ServiceUnavailableError, match="model configuration is invalid"):
        await resolve_native_model_route(
            workspace_id="workspace-1", default_model="ollama/gemma4:26b"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model,key", [("openai/gpt-4.1", "synthetic-key"), ("ollama/gemma4:26b", None)]
)
async def test_byok_identity_comes_from_the_same_override(monkeypatch, model, key):
    lookup = AsyncMock(
        return_value={
            "model": model,
            "api_key": key,
            "credential_ref": "user-model-generation:synthetic",
        }
    )
    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_native_model_override", lookup
    )
    monkeypatch.setenv("INTEGRAL_NATIVE_CREDENTIAL_REF", "other-host-generation")
    route = await resolve_native_model_route(
        workspace_id="workspace-1", default_model="openai/gpt-4.1"
    )
    if key:
        assert route.credential_ref == "user-model-generation:synthetic"
    else:
        assert route.credential_ref.startswith("local-model-generation:")
    lookup.assert_awaited_once_with("workspace-1", include_credential_identity=True)


@pytest.mark.asyncio
async def test_keyless_workspace_profile_does_not_invent_daemon_identity(monkeypatch):
    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_native_model_override",
        AsyncMock(
            return_value={
                "model": "ollama/gemma4:26b",
                "credential_ref": "user-model-generation:synthetic",
            }
        ),
    )
    route = await resolve_native_model_route(
        workspace_id="workspace-1", default_model="openai/gpt-4.1"
    )
    assert route.credential_ref is None


@pytest.mark.asyncio
async def test_local_endpoint_change_changes_attribution(monkeypatch):
    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_native_model_override",
        AsyncMock(return_value=None),
    )
    monkeypatch.setenv("INTEGRAL_NATIVE_CREDENTIAL_REF", "daemon-generation-v1")
    monkeypatch.setenv("OLLAMA_API_BASE", "http://127.0.0.1:11434/")
    first = await resolve_native_model_route(
        workspace_id="workspace-1", default_model="ollama/gemma4:26b"
    )
    monkeypatch.setenv("OLLAMA_API_BASE", "http://127.0.0.1:11435/")
    second = await resolve_native_model_route(
        workspace_id="workspace-1", default_model="ollama/gemma4:26b"
    )
    assert first.credential_ref != second.credential_ref


@pytest.mark.asyncio
async def test_workspace_byok_route_uses_resolved_litellm_route(monkeypatch) -> None:
    """Workspace model credentials select the exact LiteLLM provider route."""

    async def resolve(workspace_id: str, *, include_credential_identity=False):
        assert workspace_id == "workspace-1"
        return {
            "model": "openrouter/anthropic/claude-sonnet",
            "api_key": "workspace-secret",
        }

    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_native_model_override",
        resolve,
    )
    route = await resolve_native_model_route(
        workspace_id="workspace-1", default_model="openai/gpt-4.1"
    )

    assert route.provider == "openrouter"
    assert route.model == "openrouter/anthropic/claude-sonnet"
    assert route.api_key.get_secret_value() == "workspace-secret"
    assert route.credential_source == "workspace_byok"


@pytest.mark.asyncio
async def test_deployment_route_keeps_platform_secret_in_litellm_environment(
    monkeypatch,
) -> None:
    """Deployment credentials remain environment-owned and absent from Core."""

    async def resolve(workspace_id: str, *, include_credential_identity=False):
        return None

    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_native_model_override",
        resolve,
    )
    route = await resolve_native_model_route(
        workspace_id="workspace-1", default_model="openai/gpt-4.1"
    )

    assert route.provider == "openai"
    assert route.model == "openai/gpt-4.1"
    assert route.api_key is None
    assert route.credential_source == "platform"


@pytest.mark.asyncio
async def test_local_ollama_route_uses_configured_local_api_base(monkeypatch) -> None:
    """Local Ollama uses LiteLLM's native chat adapter for typed events."""

    async def resolve(workspace_id: str, *, include_credential_identity=False):
        return {"model": "ollama/gemma4:26b", "api_key": None}

    monkeypatch.setenv("OLLAMA_API_BASE", "http://127.0.0.1:11434/")
    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_native_model_override",
        resolve,
    )
    route = await resolve_native_model_route(
        workspace_id="workspace-1", default_model="openai/gpt-4.1"
    )

    assert route.provider == "ollama_chat"
    assert route.model == "ollama_chat/gemma4:26b"
    assert route.api_base == "http://127.0.0.1:11434"
    assert route.api_key is None
    assert route.credential_source == "local"
    assert route.ollama_num_ctx == 32768
    assert route.ollama_num_predict == 8192


@pytest.mark.asyncio
async def test_platform_ollama_route_uses_typed_native_chat_adapter(
    monkeypatch,
) -> None:
    """Deployment Ollama routes preserve reasoning and tool-call part types."""

    async def resolve(workspace_id: str, *, include_credential_identity=False):
        return None

    monkeypatch.setenv("OLLAMA_API_BASE", "http://127.0.0.1:11434/")
    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_native_model_override",
        resolve,
    )
    route = await resolve_native_model_route(
        workspace_id="workspace-1", default_model="ollama/gemma4:26b"
    )

    assert route.provider == "ollama_chat"
    assert route.model == "ollama_chat/gemma4:26b"
    assert route.api_base == "http://127.0.0.1:11434"
    assert route.api_key is None
    assert route.credential_source == "local"
    assert route.ollama_num_ctx == 32768
    assert route.ollama_num_predict == 8192


@pytest.mark.asyncio
async def test_local_ollama_context_size_is_configurable(monkeypatch) -> None:
    """The deployment can tune local context without changing other providers."""

    async def resolve(workspace_id: str, *, include_credential_identity=False):
        return None

    monkeypatch.setenv("INTEGRAL_NATIVE_OLLAMA_NUM_CTX", "12288")
    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_native_model_override",
        resolve,
    )
    route = await resolve_native_model_route(
        workspace_id="workspace-1", default_model="ollama/gemma4:26b"
    )

    assert route.ollama_num_ctx == 12288


@pytest.mark.asyncio
async def test_local_ollama_output_budget_is_configurable(monkeypatch) -> None:
    """The local generation limit is explicit and independently tunable."""

    async def resolve(workspace_id: str, *, include_credential_identity=False):
        return None

    monkeypatch.setenv("INTEGRAL_NATIVE_OLLAMA_NUM_PREDICT", "12288")
    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_native_model_override",
        resolve,
    )
    route = await resolve_native_model_route(
        workspace_id="workspace-1", default_model="ollama/gemma4:26b"
    )

    assert route.ollama_num_predict == 12288


@pytest.mark.asyncio
async def test_local_ollama_model_controls_are_optional_and_configurable(
    monkeypatch,
) -> None:
    """Model-specific thinking parameters are explicit route configuration."""

    async def resolve(workspace_id: str, *, include_credential_identity=False):
        return None

    monkeypatch.setenv("INTEGRAL_NATIVE_OLLAMA_THINK", "low")
    monkeypatch.setenv("INTEGRAL_NATIVE_OLLAMA_CLEAR_THINKING", "true")
    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_native_model_override",
        resolve,
    )
    route = await resolve_native_model_route(
        workspace_id="workspace-1", default_model="ollama/glm-5.3:cloud"
    )

    assert route.ollama_think == "low"
    assert route.ollama_clear_thinking is True


@pytest.mark.asyncio
async def test_local_ollama_model_controls_are_omitted_by_default(monkeypatch) -> None:
    """Models that do not support custom thinking modes receive no override."""

    async def resolve(workspace_id: str, *, include_credential_identity=False):
        return None

    monkeypatch.delenv("INTEGRAL_NATIVE_OLLAMA_THINK", raising=False)
    monkeypatch.delenv("INTEGRAL_NATIVE_OLLAMA_CLEAR_THINKING", raising=False)
    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_native_model_override",
        resolve,
    )
    route = await resolve_native_model_route(
        workspace_id="workspace-1", default_model="ollama/gemma4:26b"
    )

    assert route.ollama_think is None
    assert route.ollama_clear_thinking is None


@pytest.mark.asyncio
async def test_local_ollama_clear_thinking_rejects_invalid_boolean(monkeypatch) -> None:
    """Invalid operator configuration fails before a provider request."""

    async def resolve(workspace_id: str, *, include_credential_identity=False):
        return None

    monkeypatch.setenv("INTEGRAL_NATIVE_OLLAMA_CLEAR_THINKING", "sometimes")
    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_native_model_override",
        resolve,
    )
    with pytest.raises(ServiceUnavailableError, match="model configuration is invalid"):
        await resolve_native_model_route(
            workspace_id="workspace-1", default_model="ollama/glm-5.3:cloud"
        )


@pytest.mark.asyncio
async def test_local_ollama_context_must_leave_room_for_prompt(monkeypatch) -> None:
    """Reject context/output settings that would silently truncate replies."""

    async def resolve(workspace_id: str, *, include_credential_identity=False):
        return None

    monkeypatch.setenv("INTEGRAL_NATIVE_OLLAMA_NUM_CTX", "8192")
    monkeypatch.setenv("INTEGRAL_NATIVE_OLLAMA_NUM_PREDICT", "8192")
    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_native_model_override",
        resolve,
    )
    with pytest.raises(ServiceUnavailableError, match="model configuration is invalid"):
        await resolve_native_model_route(
            workspace_id="workspace-1", default_model="ollama/gemma4:26b"
        )


@pytest.mark.asyncio
async def test_invalid_local_ollama_output_budget_fails_closed(monkeypatch) -> None:
    """Invalid local output limits fail before the provider request."""

    async def resolve(workspace_id: str, *, include_credential_identity=False):
        return None

    monkeypatch.setenv("INTEGRAL_NATIVE_OLLAMA_NUM_PREDICT", "not-an-integer")
    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_native_model_override",
        resolve,
    )
    with pytest.raises(ServiceUnavailableError, match="model configuration is invalid"):
        await resolve_native_model_route(
            workspace_id="workspace-1", default_model="ollama/gemma4:26b"
        )


@pytest.mark.asyncio
async def test_invalid_local_ollama_context_size_fails_closed(monkeypatch) -> None:
    """Invalid local context settings fail before the request reaches Ollama."""

    async def resolve(workspace_id: str, *, include_credential_identity=False):
        return None

    monkeypatch.setenv("INTEGRAL_NATIVE_OLLAMA_NUM_CTX", "not-an-integer")
    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_native_model_override",
        resolve,
    )
    with pytest.raises(ServiceUnavailableError, match="model configuration is invalid"):
        await resolve_native_model_route(
            workspace_id="workspace-1", default_model="ollama/gemma4:26b"
        )


@pytest.mark.asyncio
async def test_invalid_default_model_route_fails_when_no_workspace_override(
    monkeypatch,
) -> None:
    """Workspace credentials take precedence; a malformed fallback still fails."""
    called = False

    async def resolve(workspace_id: str, *, include_credential_identity=False):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_native_model_override",
        resolve,
    )
    with pytest.raises(ServiceUnavailableError, match="model configuration is invalid"):
        await resolve_native_model_route(
            workspace_id="workspace-1", default_model="gpt-4.1"
        )
    assert called is True


@pytest.mark.asyncio
async def test_native_platform_route_honors_host_quota_denial(monkeypatch):
    from app.api.errors import QuotaExceededError

    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_native_model_override",
        AsyncMock(return_value=None),
    )
    gate = AsyncMock(side_effect=QuotaExceededError(message="Host quota reached"))
    monkeypatch.setattr("app.services.host_hooks.assert_platform_quota", gate)
    with pytest.raises(QuotaExceededError, match="Host quota reached"):
        await resolve_native_model_route(
            workspace_id="workspace-1", default_model="openai/gpt-4.1"
        )
    gate.assert_awaited_once_with("workspace-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("local", [True, False])
async def test_native_local_and_byok_routes_bypass_platform_quota(monkeypatch, local):
    override = None if local else {"model": "openai/gpt-4.1", "api_key": "byok-secret"}
    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_native_model_override",
        AsyncMock(return_value=override),
    )
    gate = AsyncMock()
    monkeypatch.setattr("app.services.host_hooks.assert_platform_quota", gate)
    route = await resolve_native_model_route(
        workspace_id="workspace-1",
        default_model="ollama/gemma4" if local else "openai/gpt-4.1",
    )
    assert route.credential_source == ("local" if local else "workspace_byok")
    gate.assert_not_awaited()


@pytest.mark.asyncio
async def test_workspace_model_works_without_a_deployment_model(monkeypatch):
    lookup = AsyncMock(
        return_value={
            "model": "openai/gpt-test",
            "api_key": "synthetic",
            "credential_ref": "test:v1",
        }
    )
    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_native_model_override", lookup
    )
    route = await resolve_native_model_route(
        workspace_id="workspace-1", default_model=""
    )
    assert route.model == "openai/gpt-test"
    assert route.credential_source == "workspace_byok"
    lookup.assert_awaited_once_with("workspace-1", include_credential_identity=True)


@pytest.mark.asyncio
async def test_missing_model_produces_setup_error_without_harness_fallback(monkeypatch):
    from app.api.errors import ServiceUnavailableError

    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_native_model_override",
        AsyncMock(return_value=None),
    )
    with pytest.raises(ServiceUnavailableError, match="Integral AI needs a model"):
        await resolve_native_model_route(workspace_id="workspace-1", default_model="")


def test_unconfigured_native_model_has_actionable_chat_error():
    from app.api.errors import ServiceUnavailableError
    from app.services.chat_streaming import classify_turn_exception

    error = ServiceUnavailableError(
        message="private debug detail",
        details={"reason": "native_model_not_configured"},
    )
    code, message = classify_turn_exception(error)
    assert code == "model_setup_required"
    assert "Settings" in message and "Ollama" in message
    assert "private debug detail" not in message


@pytest.mark.asyncio
async def test_workspace_ollama_cloud_uses_cloud_even_with_local_server_env(
    monkeypatch,
):
    monkeypatch.setenv("OLLAMA_API_BASE", "http://127.0.0.1:11434")
    monkeypatch.setenv("INTEGRAL_NATIVE_OLLAMA_NUM_CTX", "invalid-local-setting")
    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_native_model_override",
        AsyncMock(
            return_value={
                "model": "ollama/gpt-oss:120b",
                "api_key": "synthetic-cloud-key",
                "credential_ref": "user-model-generation:cloud",
            }
        ),
    )
    route = await resolve_native_model_route(
        workspace_id="workspace-1", default_model=""
    )
    assert route.provider == "ollama_chat"
    assert route.model == "ollama_chat/gpt-oss:120b"
    assert route.api_base == "https://ollama.com"
    assert route.credential_source == "workspace_byok"
    assert route.credential_ref == "user-model-generation:cloud"
    assert route.api_key.get_secret_value() == "synthetic-cloud-key"
    assert route.ollama_num_ctx is None
