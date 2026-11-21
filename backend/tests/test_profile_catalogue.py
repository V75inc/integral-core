"""Installed capability discovery and execution use current workspace authority."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentive.services import profile_catalogue as catalogue
from app.agentive.services.connection_profiles import ProfileDenied
from app.api.errors import InsufficientPermissionsError


@pytest.mark.asyncio
async def test_installed_capabilities_withdraw_on_authority_or_lifecycle_change(
    monkeypatch,
):
    monkeypatch.setattr(catalogue, "list_core_skills", lambda: [])
    app = SimpleNamespace(app_id="app", name="Installed app")
    skill = SimpleNamespace(
        name="app__review",
        description="Review instructions",
        body="Review records.",
        metadata={"app_id": "app"},
    )
    compose = AsyncMock(
        return_value=SimpleNamespace(apps=[app], overlay_skill_docs=[skill])
    )
    current = SimpleNamespace(workspace_id="ws", lifecycle_state="active")
    monkeypatch.setattr(catalogue.App, "get", AsyncMock(return_value=current))
    monkeypatch.setattr(catalogue, "compose_workspace_agent_profile", compose)
    monkeypatch.setattr(
        catalogue, "can_access_workspace", AsyncMock(return_value="member")
    )
    listed = AsyncMock(
        return_value={
            "queries": [
                {
                    "key": "read",
                    "name": "Read",
                    "description": "Read scoped records",
                    "input_schema": {"type": "object"},
                    "policy_action": "app.read",
                }
            ]
        }
    )
    monkeypatch.setattr(catalogue, "list_app_queries", listed)
    evaluate = AsyncMock(return_value=SimpleNamespace(allowed=True))
    monkeypatch.setattr(catalogue, "policy_evaluate", evaluate)
    invoke = AsyncMock(return_value={"output": {"records": []}})
    monkeypatch.setattr(catalogue, "invoke_app_query", invoke)

    assert await catalogue.installed_catalogue("ws", "visitor", "public") == {}
    compose.assert_not_awaited()
    offered = await catalogue.installed_catalogue("ws", "staff", "staff")
    query = next(
        name for name, cap in offered.items() if cap.declaration["kind"] == "tool"
    )
    assert {cap.declaration["kind"] for cap in offered.values()} == {"tool", "skill"}
    assert await catalogue.invoke_installed("ws", "staff", "staff", query, {}) == {
        "records": []
    }
    invoke.assert_awaited_once_with(
        user_id="staff", workspace_id="ws", app_id="app", query_key="read", params={}
    )

    evaluate.return_value.allowed = False
    with pytest.raises(ProfileDenied):
        await catalogue.invoke_installed("ws", "staff", "staff", query, {})
    evaluate.return_value.allowed = True
    listed.side_effect = InsufficientPermissionsError("App access withdrawn")
    assert await catalogue.installed_catalogue("ws", "staff", "staff") == {}
    listed.side_effect = None
    current.lifecycle_state = "uninstalled"
    assert await catalogue.installed_catalogue("ws", "staff", "staff") == {}
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_public_cannot_load_core_or_custom_skill_and_custom_revokes(monkeypatch):
    core = {
        "id": "core:integral-scaffold",
        "name": "Scaffold",
        "description": "Internal",
        "resolved_body": "Internal instructions.",
    }
    monkeypatch.setattr(catalogue, "list_core_skills", lambda: [core])
    doc = SimpleNamespace(
        description="Workspace procedure",
        body="Custom instructions.",
        metadata={
            "origin": "workspace",
            "skill_id": "custom",
            "skill_name": "Custom procedure",
        },
    )
    profile = SimpleNamespace(apps=[], overlay_skill_docs=[doc])
    monkeypatch.setattr(
        catalogue, "compose_workspace_agent_profile", AsyncMock(return_value=profile)
    )
    monkeypatch.setattr(
        catalogue, "can_access_workspace", AsyncMock(return_value="member")
    )
    offered = await catalogue.installed_catalogue("ws", "staff", "staff")
    assert {cap.declaration["source"] for cap in offered.values()} == {
        "core",
        "workspace",
    }
    for key in offered:
        with pytest.raises(ProfileDenied):
            await catalogue.invoke_installed("ws", "visitor", "public", key, {})
    custom_key = next(
        key for key, cap in offered.items() if cap.declaration["source"] == "workspace"
    )
    assert await catalogue.invoke_installed("ws", "staff", "staff", custom_key, {}) == {
        "instructions": "Custom instructions."
    }
    profile.overlay_skill_docs = []
    with pytest.raises(ProfileDenied):
        await catalogue.invoke_installed("ws", "staff", "staff", custom_key, {})
