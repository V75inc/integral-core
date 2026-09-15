"""Agent-facing app deletion — was completely missing (no integral_delete_app
tool existed at all, so "delete this app" via chat had no tool to call).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.agentive.staging_executors import dispatch
from app.agentive.tooling import bindings


@pytest.mark.asyncio
async def test_stage_delete_app_uses_app_name(monkeypatch):
    app_id = "n.App.abc123def456"
    monkeypatch.setattr(
        bindings._sd,
        "load_app_record",
        AsyncMock(return_value={"id": app_id, "name": "Vendor Contracts"}),
    )

    staged = await bindings._stage_delete_app({"app_id": app_id})

    assert staged["kind"] == "delete_app"
    assert staged["summary"] == "Delete app “Vendor Contracts”"
    assert "**Delete app** *Vendor Contracts*" in staged["diff_human"]
    assert "every track it contains" in staged["diff_human"]
    assert app_id not in staged["summary"]
    assert staged["payload"] == {"app_id": app_id}


@pytest.mark.asyncio
async def test_stage_delete_app_requires_app_id():
    with pytest.raises(ValueError):
        await bindings._stage_delete_app({})


def test_delete_app_tool_bound_and_dispatchable():
    binding = bindings.TOOL_BINDINGS.get("integral_delete_app")
    assert binding is not None, "integral_delete_app missing from TOOL_BINDINGS"
    assert binding.stager is bindings._stage_delete_app


def test_delete_app_is_a_delete_kind():
    """Deletes must never auto-apply via a routine's write_scope (v1 hard rule
    — see _DELETE_KINDS in staging_executors.py)."""
    from app.agentive.staging_executors import _DELETE_KINDS

    assert "delete_app" in _DELETE_KINDS


@pytest.mark.asyncio
async def test_dispatch_delete_app_calls_the_rest_handler(monkeypatch):
    """The executor delegates to the same DELETE /api/apps/{id} handler a
    human uses — no separate deletion logic to duplicate/drift."""
    called = {}

    async def fake_handler(request, app_id):
        called["app_id"] = app_id
        return {"message": "App deleted successfully", "deleted_app_id": app_id}

    monkeypatch.setattr("app.api.apps.delete_app", fake_handler)

    async def fake_resolve_auth_user(user_id):
        return object()

    monkeypatch.setattr(
        "app.agentive.staging_executors._resolve_auth_user", fake_resolve_auth_user
    )

    result = await dispatch(
        user_id="u1", kind="delete_app", payload={"app_id": "n.App.xyz"}
    )

    assert result.get("deleted_app_id") == "n.App.xyz"
    assert called["app_id"] == "n.App.xyz"


@pytest.mark.asyncio
async def test_delete_app_cascade_removes_accompanying_skill():
    from app.agentive.services.agent_skills import create_workspace_skill
    from app.models.nodes import App, Skill, Workspace
    from app.services.app_deletion import delete_app_cascade

    ws = await Workspace.create(name="WS-Cascade", kind="personal")
    app = await App.create(
        name="App With Skill", name_fold="app with skill", workspace_id=ws.id
    )
    skill = await create_workspace_skill(
        workspace_id=ws.id,
        user_id="u1",
        key="cascade_me",
        name="Cascade Me",
        description="Should die with the app.",
        body_override="# SOP",
        tools_required=[],
        app_id=app.id,
    )
    skill_id = skill.id

    await delete_app_cascade(app)

    assert await Skill.get(skill_id) is None


@pytest.mark.asyncio
async def test_delete_app_cascade_no_tracks_still_removes_skill():
    """Exercises the early-return (no-tracks) branch of delete_app_cascade."""
    from app.agentive.services.agent_skills import create_workspace_skill
    from app.models.nodes import App, Skill, Workspace
    from app.services.app_deletion import delete_app_cascade

    ws = await Workspace.create(name="WS-Cascade2", kind="personal")
    app = await App.create(
        name="Empty App With Skill",
        name_fold="empty app with skill",
        workspace_id=ws.id,
    )
    skill = await create_workspace_skill(
        workspace_id=ws.id,
        user_id="u1",
        key="cascade_me_2",
        name="Cascade Me 2",
        description="No tracks on this app; still should die.",
        body_override="# SOP",
        tools_required=[],
        app_id=app.id,
    )
    skill_id = skill.id

    await delete_app_cascade(app)

    assert await Skill.get(skill_id) is None


@pytest.mark.asyncio
async def test_delete_app_cascade_with_tracks_still_removes_skill():
    """Exercises the main (has-tracks) branch of delete_app_cascade, not just
    the no-tracks early return — proving the skill cleanup at the END of the
    function (after track deletion) also runs."""
    from datetime import datetime, timezone

    from app.agentive.services.agent_skills import create_workspace_skill
    from app.models.edges import CONTAINS
    from app.models.nodes import App, Skill, Track, Workspace
    from app.services.app_deletion import delete_app_cascade

    ws = await Workspace.create(name="WS-Cascade3", kind="personal")
    app = await App.create(
        name="App With Track And Skill",
        name_fold="app with track and skill",
        workspace_id=ws.id,
    )
    track = await Track.create(title="Some Track", workspace_id=ws.id)
    await app.connect(
        track, edge=CONTAINS, added_at=datetime.now(timezone.utc).isoformat()
    )

    skill = await create_workspace_skill(
        workspace_id=ws.id,
        user_id="u1",
        key="cascade_me_3",
        name="Cascade Me 3",
        description="App has a track; skill should still die.",
        body_override="# SOP",
        tools_required=[],
        app_id=app.id,
    )
    skill_id = skill.id

    await delete_app_cascade(app)

    assert await Skill.get(skill_id) is None
