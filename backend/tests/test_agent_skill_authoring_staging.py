"""Staging plumbing for the three agent skill-authoring kinds."""

from __future__ import annotations

import pytest

from app.agentive.staging_executors import dispatch
from app.models.nodes import Skill, Workspace
from app.services.agent_scope import current_scope_workspace_id


async def _make_workspace() -> Workspace:
    return await Workspace.create(name="Staging Skills WS", kind="organization")


async def _true(user_id: str, workspace_id: str) -> bool:
    return True


@pytest.mark.asyncio
async def test_dispatch_author_skill(monkeypatch):
    from app.services import agent_skill_authoring

    monkeypatch.setattr(agent_skill_authoring, "is_workspace_admin_or_owner", _true)
    ws = await _make_workspace()
    token = current_scope_workspace_id.set(ws.id)
    try:
        result = await dispatch(
            user_id="u1",
            kind="author_skill",
            payload={
                "key": "dispatched_skill",
                "name": "Dispatched Skill",
                "description": "Created via staging dispatch.",
                "body_override": "# Dispatched Skill",
                "tools_required": [],
            },
        )
    finally:
        current_scope_workspace_id.reset(token)

    assert "error" not in result
    skill = await Skill.get(result["skill_id"])
    assert skill.key == "dispatched_skill"


@pytest.mark.asyncio
async def test_dispatch_update_and_delete_skill(monkeypatch):
    from app.services import agent_skill_authoring

    monkeypatch.setattr(agent_skill_authoring, "is_workspace_admin_or_owner", _true)
    ws = await _make_workspace()
    skill = await Skill.create(
        app_id="",
        workspace_id=ws.id,
        key="update_me",
        name="Update Me",
        description="d",
        kind="declarative",
        origin="workspace",
        trust_tier="untrusted",
        body_override="# Update Me",
        tools_required=[],
        enabled=True,
    )

    token = current_scope_workspace_id.set(ws.id)
    try:
        update_result = await dispatch(
            user_id="u1",
            kind="update_skill",
            payload={"skill_id": skill.id, "name": "Updated Name"},
        )
        assert "error" not in update_result
        refreshed = await Skill.get(skill.id)
        assert refreshed.name == "Updated Name"

        delete_result = await dispatch(
            user_id="u1",
            kind="delete_skill",
            payload={"skill_id": skill.id},
        )
    finally:
        current_scope_workspace_id.reset(token)

    assert delete_result["ok"] is True
    assert await Skill.get(skill.id) is None


@pytest.mark.asyncio
async def test_dispatch_author_skill_forwards_app_id_and_private(monkeypatch):
    from app.services import agent_skill_authoring

    monkeypatch.setattr(agent_skill_authoring, "is_workspace_admin_or_owner", _true)

    called = {}

    async def fake_author(
        *,
        user_id,
        workspace_id,
        name,
        description,
        body_override,
        key=None,
        tools_required=None,
        app_id=None,
        private=None,
    ):
        called["app_id"] = app_id
        called["private"] = private
        return {"skill_id": "n.Skill.new", "key": "vendor_sop"}

    monkeypatch.setattr(
        "app.services.agent_skill_authoring.author_skill_for_agent", fake_author
    )

    ws = await _make_workspace()
    token = current_scope_workspace_id.set(ws.id)
    try:
        result = await dispatch(
            user_id="u1",
            kind="author_skill",
            payload={
                "name": "Vendor SOP",
                "description": "desc",
                "body_override": "# SOP",
                "app_id": "n.App.xyz",
                "private": True,
            },
        )
    finally:
        current_scope_workspace_id.reset(token)

    assert called["app_id"] == "n.App.xyz"
    assert called["private"] is True
    assert result["skill_id"] == "n.Skill.new"
