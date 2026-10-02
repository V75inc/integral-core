"""Provider API-key validation probes (BYOK test connection)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.model_credentials import validate_provider_api_key


def _mock_client(*, get_status=200, post_status=200):
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    client.get = AsyncMock(return_value=MagicMock(status_code=get_status, text=""))
    client.post = AsyncMock(return_value=MagicMock(status_code=post_status, text=""))
    return client


@pytest.mark.asyncio
async def test_openai_validates_via_models_list():
    """Validate an OpenAI key via the /models probe; HTTP 200 → validated."""
    with patch(
        "app.services.model_credentials.httpx.AsyncClient",
        return_value=_mock_client(get_status=200),
    ):
        ok, msg = await validate_provider_api_key("openai", "sk-test-key-1234")
    assert ok is True
    assert msg == "validated"


@pytest.mark.asyncio
async def test_anthropic_rejects_unauthorized():
    """Anthropic key probe returning 401 → invalid API key."""
    with patch(
        "app.services.model_credentials.httpx.AsyncClient",
        return_value=_mock_client(get_status=401),
    ):
        ok, msg = await validate_provider_api_key("anthropic", "sk-ant-test-key")
    assert ok is False
    assert msg == "invalid API key"


@pytest.mark.asyncio
async def test_openrouter_validates_via_auth_key_endpoint():
    """Validate an OpenRouter key via the /auth/key endpoint; 200 → validated."""
    client = _mock_client(get_status=200)
    with patch(
        "app.services.model_credentials.httpx.AsyncClient",
        return_value=client,
    ):
        ok, msg = await validate_provider_api_key(
            "openrouter", "sk-or-v1-test-key-1234567890"
        )
    assert ok is True
    client.get.assert_awaited_once()
    assert client.get.await_args.args[0].endswith("/auth/key")


@pytest.mark.asyncio
async def test_ollama_validates_via_chat_probe():
    """Ollama key probes /api/chat via POST; 200 → validated."""
    client = _mock_client(post_status=200)
    with patch(
        "app.services.model_credentials.httpx.AsyncClient",
        return_value=client,
    ):
        ok, msg = await validate_provider_api_key("ollama", "ollama-test-key-12345")
    assert ok is True
    client.post.assert_awaited_once()
    assert client.post.await_args.args[0].endswith("/api/chat")


@pytest.mark.asyncio
async def test_ollama_accepts_model_not_found_as_valid_key():
    """Ollama 404 (model missing) still proves the key is valid."""
    with patch(
        "app.services.model_credentials.httpx.AsyncClient",
        return_value=_mock_client(post_status=404),
    ):
        ok, msg = await validate_provider_api_key("ollama", "ollama-test-key-12345")
    assert ok is True
    assert msg == "validated"


@pytest.mark.asyncio
async def test_ollama_rejects_unauthorized():
    """Ollama 401 → invalid API key."""
    with patch(
        "app.services.model_credentials.httpx.AsyncClient",
        return_value=_mock_client(post_status=401),
    ):
        ok, msg = await validate_provider_api_key("ollama", "bad-key")
    assert ok is False
    assert msg == "invalid API key"


@pytest.mark.asyncio
async def test_local_ollama_validates_reachable_tags_endpoint(monkeypatch):
    """Local Ollama checks the configured daemon without an auth header."""
    client = _mock_client(get_status=200)
    monkeypatch.setenv("OLLAMA_API_BASE", "http://ollama-host:11434/")
    with patch(
        "app.services.model_credentials.httpx.AsyncClient",
        return_value=client,
    ):
        ok, msg = await validate_provider_api_key("ollama_local", "")
    assert (ok, msg) == (True, "local Ollama reachable")
    client.get.assert_awaited_once_with("http://ollama-host:11434/api/tags")


@pytest.mark.asyncio
async def test_local_ollama_rejects_api_keys():
    """Local mode never accepts or stores a hosted API credential."""
    ok, msg = await validate_provider_api_key("ollama_local", "not-a-local-key")
    assert ok is False
    assert msg == "Local Ollama does not use an API key"
