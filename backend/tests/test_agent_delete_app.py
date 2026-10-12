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


async def _owned_cascade_app(name: str):
    from app.models.nodes import App
    from app.services.app_graph import catalog_app
    from tests.fixtures.workspaces import make_org_workspace, workspace_owner_id

    workspace = await make_org_workspace(name + " workspace")
    owner_id = await workspace_owner_id(workspace)
    app = await App.create(
        name=name, name_fold=name.casefold(), workspace_id=workspace.id
    )
    await catalog_app(app)
    return owner_id, workspace, app


@pytest.mark.asyncio
@pytest.mark.parametrize("with_track", [False, True])
async def test_current_owner_cascade_removes_accompanying_skill(with_track):
    from app.agentive.services.agent_skills import create_workspace_skill
    from app.models.edges import CONTAINS
    from app.models.nodes import Skill, Track
    from app.services.app_deletion import delete_app_cascade
    from app.services.app_graph import catalog_track

    owner_id, workspace, app = await _owned_cascade_app("Owned skill cascade")
    if with_track:
        track = await Track.create(
            title="Owned cascade track", workspace_id=workspace.id
        )
        await app.connect(track, edge=CONTAINS)
        await catalog_track(track)
    skill = await create_workspace_skill(
        workspace_id=workspace.id,
        user_id=owner_id,
        key="cascade_owned",
        name="Cascade owned",
        description="Owner removes named subtree",
        body_override="# SOP",
        tools_required=[],
        app_id=app.id,
    )
    await delete_app_cascade(app, actor_user_id=owner_id)
    assert await Skill.get(skill.id) is None


@pytest.mark.asyncio
async def test_empty_actor_cascade_never_deletes_app():
    from app.api.errors import InsufficientPermissionsError
    from app.models.nodes import App
    from app.services.app_deletion import delete_app_cascade

    _, _, app = await _owned_cascade_app("No actor cascade")
    with pytest.raises(InsufficientPermissionsError):
        await delete_app_cascade(app)
    assert await App.find_one({"id": app.id}) is not None
