"""Trusted credential resolver to native route contract tests."""

from __future__ import annotations

import pytest

from app.agentive.harness.model_route import resolve_native_model_route


@pytest.mark.asyncio
async def test_workspace_byok_route_uses_resolved_litellm_route(monkeypatch) -> None:
    """Workspace model credentials select the exact LiteLLM provider route."""

    async def resolve(workspace_id: str):
        assert workspace_id == "workspace-1"
        return {
            "slots": {
                "default": {
                    "provider": "litellm",
                    "model": "openrouter/anthropic/claude-sonnet",
                    "api_key": "workspace-secret",
                }
            }
        }

    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_agent_model_override",
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

    async def resolve(workspace_id: str):
        return None

    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_agent_model_override",
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

    async def resolve(workspace_id: str):
        return {
            "slots": {
                "default": {
                    "provider": "ollama_local",
                    "model": "ollama/gemma4:26b",
                    "api_key": None,
                }
            }
        }

    monkeypatch.setenv("OLLAMA_API_BASE", "http://127.0.0.1:11434/")
    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_agent_model_override",
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

    async def resolve(workspace_id: str):
        return None

    monkeypatch.setenv("OLLAMA_API_BASE", "http://127.0.0.1:11434/")
    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_agent_model_override",
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

    async def resolve(workspace_id: str):
        return None

    monkeypatch.setenv("INTEGRAL_NATIVE_OLLAMA_NUM_CTX", "12288")
    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_agent_model_override",
        resolve,
    )
    route = await resolve_native_model_route(
        workspace_id="workspace-1", default_model="ollama/gemma4:26b"
    )

    assert route.ollama_num_ctx == 12288


@pytest.mark.asyncio
async def test_local_ollama_output_budget_is_configurable(monkeypatch) -> None:
    """The local generation limit is explicit and independently tunable."""

    async def resolve(workspace_id: str):
        return None

    monkeypatch.setenv("INTEGRAL_NATIVE_OLLAMA_NUM_PREDICT", "12288")
    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_agent_model_override",
        resolve,
    )
    route = await resolve_native_model_route(
        workspace_id="workspace-1", default_model="ollama/gemma4:26b"
    )

    assert route.ollama_num_predict == 12288


@pytest.mark.asyncio
async def test_local_ollama_context_must_leave_room_for_prompt(monkeypatch) -> None:
    """Reject context/output settings that would silently truncate replies."""

    async def resolve(workspace_id: str):
        return None

    monkeypatch.setenv("INTEGRAL_NATIVE_OLLAMA_NUM_CTX", "8192")
    monkeypatch.setenv("INTEGRAL_NATIVE_OLLAMA_NUM_PREDICT", "8192")
    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_agent_model_override",
        resolve,
    )
    with pytest.raises(ValueError, match="leave room for the prompt"):
        await resolve_native_model_route(
            workspace_id="workspace-1", default_model="ollama/gemma4:26b"
        )


@pytest.mark.asyncio
async def test_invalid_local_ollama_output_budget_fails_closed(monkeypatch) -> None:
    """Invalid local output limits fail before the provider request."""

    async def resolve(workspace_id: str):
        return None

    monkeypatch.setenv("INTEGRAL_NATIVE_OLLAMA_NUM_PREDICT", "not-an-integer")
    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_agent_model_override",
        resolve,
    )
    with pytest.raises(ValueError, match="must be an integer"):
        await resolve_native_model_route(
            workspace_id="workspace-1", default_model="ollama/gemma4:26b"
        )


@pytest.mark.asyncio
async def test_invalid_local_ollama_context_size_fails_closed(monkeypatch) -> None:
    """Invalid local context settings fail before the request reaches Ollama."""

    async def resolve(workspace_id: str):
        return None

    monkeypatch.setenv("INTEGRAL_NATIVE_OLLAMA_NUM_CTX", "not-an-integer")
    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_agent_model_override",
        resolve,
    )
    with pytest.raises(ValueError, match="must be an integer"):
        await resolve_native_model_route(
            workspace_id="workspace-1", default_model="ollama/gemma4:26b"
        )


@pytest.mark.asyncio
async def test_invalid_default_model_route_fails_before_credential_lookup(
    monkeypatch,
) -> None:
    """An untrusted or malformed model name is never sent to the resolver."""
    called = False

    async def resolve(workspace_id: str):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(
        "app.services.model_credential_resolver.resolve_agent_model_override",
        resolve,
    )
    with pytest.raises(ValueError, match="provider/model"):
        await resolve_native_model_route(
            workspace_id="workspace-1", default_model="gpt-4.1"
        )
    assert called is False
