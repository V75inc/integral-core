"""Report installation faults before native persistence or paid generation."""

from types import SimpleNamespace

import pytest

from app.api.errors import ServiceUnavailableError
from app.services.chat_providers.base import ChatTurnContext
from app.services.chat_providers.pydantic_ai_provider import PydanticAIProvider
from app.services.chat_streaming import classify_turn_exception

pytestmark = [pytest.mark.unit, pytest.mark.smoke]


@pytest.mark.asyncio
async def test_unconfigured_storage_fails_before_catalogue_or_provider_dispatch(
    monkeypatch,
):
    async def thread_get(_id):
        return SimpleNamespace(
            user_id="user", workspace_id="workspace", provider_id="integral_native"
        )

    async def workspace_access(_user, _workspace):
        return "owner"

    def forbidden_catalogue():
        raise AssertionError("native preparation must stop before runtime setup")

    monkeypatch.setattr("app.models.nodes.ChatThread.get", thread_get)
    monkeypatch.setattr(
        "app.services.workspace_permissions.can_access_workspace", workspace_access
    )
    monkeypatch.setattr(
        "app.services.credential_crypto.encryption_available", lambda: False
    )
    monkeypatch.setattr(
        "app.agentive.tooling.catalogue.build_tool_catalogue", forbidden_catalogue
    )
    with pytest.raises(ServiceUnavailableError) as error:
        await PydanticAIProvider()._prepare(
            ChatTurnContext(
                user_id="user",
                user_email="test@example.test",
                workspace_id="workspace",
                thread_id="thread",
                session_id=None,
                text="hello",
            )
        )
    code, message = classify_turn_exception(error.value)
    assert code == "storage_setup_required"
    assert "original storage encryption" in message


def test_unreadable_checkpoint_reports_safe_recovery_and_never_raw_exception():
    from app.agentive.harness.jvspatial_store import HarnessPersistenceError

    error = HarnessPersistenceError(
        "private payload and secret-key must not leave process"
    )
    code, message = classify_turn_exception(error, provider=PydanticAIProvider())
    assert code == "harness_storage_unreadable"
    assert "original storage encryption" in message
    assert "private payload" not in message
    assert "secret-key" not in message
