"""Native-only registration and fresh-install model setup contracts."""

import pytest

from app.main import app as core_app
from app.services.chat_providers.pydantic_ai_provider import PydanticAIProvider
from app.services.chat_providers.registry import get_registry


def test_native_is_the_only_registered_core_harness():
    assert core_app is not None
    registry = get_registry()
    assert registry.default().id == "integral_native"
    assert registry.get("jvagent") is None
    assert [provider.id for provider in registry.list()] == ["integral_native"]


@pytest.mark.asyncio
async def test_native_catalog_exists_without_environment(monkeypatch):
    monkeypatch.delenv("INTEGRAL_NATIVE_MODEL", raising=False)
    monkeypatch.setenv("INTEGRAL_NATIVE_HARNESS_ENABLED", "false")
    provider = PydanticAIProvider()
    assert provider.is_available()
    assert (await provider.list_agents())[0]["id"] == "integral_core"


@pytest.mark.asyncio
async def test_retired_harness_cannot_create_threads_or_list_agents(
    authenticated_client,
):
    for path in ["/api/chat/providers/jvagent/agents"]:
        response = await authenticated_client.get(path)
        assert response.status_code == 400
    response = await authenticated_client.post(
        "/api/chat/threads", json={"provider_id": "jvagent"}
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_native_can_create_thread_without_model_environment(
    authenticated_client, monkeypatch
):
    monkeypatch.delenv("INTEGRAL_NATIVE_MODEL", raising=False)
    monkeypatch.delenv("INTEGRAL_NATIVE_HARNESS_ENABLED", raising=False)
    response = await authenticated_client.post(
        "/api/chat/threads", json={"provider_id": "integral_native"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["agent_id"] == "integral_core"


def test_distributable_has_no_jvagent_dependency_or_runtime_import():
    import ast
    from importlib.metadata import requires
    from pathlib import Path

    assert not any(
        item.lower().startswith("jvagent") for item in requires("integral-core") or []
    )
    app_root = Path(__file__).resolve().parents[1] / "app"
    for path in app_root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(
                    not alias.name.startswith("jvagent") for alias in node.names
                ), path
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("jvagent"), path
