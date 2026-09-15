"""Permission-checked wrappers the resident agent uses to author workspace skills.

The existing agent_skills.py service functions (create/update/delete_workspace_skill)
have no permission check of their own — today only the HTTP handlers in
app/agentive/api/agent_skills.py gate access via is_workspace_admin_or_owner
before calling them. These wrappers add that same check so an agent tool can
call the service layer directly without an unauthenticated privilege escalation
(any workspace member could otherwise create/edit/delete skills via chat).
"""

from __future__ import annotations

import pytest

from app.api.errors import InsufficientPermissionsError, ResourceNotFoundError
from app.models.nodes import App, Skill, Workspace
from app.services.agent_skill_authoring import (
    author_skill_for_agent,
    delete_skill_for_agent,
    update_skill_for_agent,
)
from app.utils.time import utc_now_iso


async def _make_workspace(kind: str = "organization") -> Workspace:
    return await Workspace.create(name="Skills WS", kind=kind)


async def _make_app(name: str, workspace_id: str = "ws_test") -> App:
    now = utc_now_iso()
    return await App.create(
        name=name,
        name_fold=name.casefold(),
        workspace_id=workspace_id,
        created_at=now,
        updated_at=now,
    )


async def _false(user_id: str, workspace_id: str) -> bool:
    return False


async def _true(user_id: str, workspace_id: str) -> bool:
    return True


@pytest.mark.asyncio
async def test_author_skill_requires_admin_or_owner(monkeypatch):
    from app.services import agent_skill_authoring

    monkeypatch.setattr(agent_skill_authoring, "is_workspace_admin_or_owner", _false)
    with pytest.raises(InsufficientPermissionsError):
        await author_skill_for_agent(
            user_id="u1",
            workspace_id="n.Workspace.fake",
            key="my_skill",
            name="My Skill",
            description="Does a thing.",
            body_override="# My Skill\n\nDoes a thing.",
        )


@pytest.mark.asyncio
async def test_author_skill_creates_skill_when_authorized(monkeypatch):
    from app.services import agent_skill_authoring

    monkeypatch.setattr(agent_skill_authoring, "is_workspace_admin_or_owner", _true)
    ws = await _make_workspace()

    result = await author_skill_for_agent(
        user_id="u1",
        workspace_id=ws.id,
        key="my_new_skill",
        name="My New Skill",
        description="Handles the widget workflow.",
        body_override="# My New Skill\n\nHandles the widget workflow.",
        tools_required=[],
    )

    assert result["key"] == "my_new_skill"
    skill = await Skill.get(result["skill_id"])
    assert skill is not None
    assert skill.origin == "workspace"
    assert skill.workspace_id == ws.id


@pytest.mark.asyncio
async def test_author_skill_derives_key_from_name_when_omitted(monkeypatch):
    """The agent reliably supplies name but often skips key entirely (observed
    regression: tool call failed with 'missing required argument(s): key')."""
    from app.services import agent_skill_authoring

    monkeypatch.setattr(agent_skill_authoring, "is_workspace_admin_or_owner", _true)
    ws = await _make_workspace()

    result = await author_skill_for_agent(
        user_id="u1",
        workspace_id=ws.id,
        name="Daily Standup Reminder!",
        description="Nudges the team to post daily standup updates.",
        body_override="# Daily Standup Reminder",
    )

    assert result["key"] == "daily_standup_reminder"


@pytest.mark.asyncio
async def test_update_skill_rejects_wrong_workspace(monkeypatch):
    from app.services import agent_skill_authoring

    monkeypatch.setattr(agent_skill_authoring, "is_workspace_admin_or_owner", _true)
    ws_a = await _make_workspace()
    ws_b = await _make_workspace()
    skill = await Skill.create(
        app_id="",
        workspace_id=ws_a.id,
        key="a_skill",
        name="A Skill",
        description="d",
        kind="declarative",
        origin="workspace",
        trust_tier="untrusted",
        body_override="# A Skill",
        tools_required=[],
        enabled=True,
    )

    with pytest.raises(ResourceNotFoundError):
        await update_skill_for_agent(
            user_id="u1",
            workspace_id=ws_b.id,
            skill_id=skill.id,
            name="Renamed",
        )


@pytest.mark.asyncio
async def test_delete_skill_removes_workspace_skill(monkeypatch):
    from app.services import agent_skill_authoring

    monkeypatch.setattr(agent_skill_authoring, "is_workspace_admin_or_owner", _true)
    ws = await _make_workspace()
    skill = await Skill.create(
        app_id="",
        workspace_id=ws.id,
        key="to_delete",
        name="To Delete",
        description="d",
        kind="declarative",
        origin="workspace",
        trust_tier="untrusted",
        body_override="# To Delete",
        tools_required=[],
        enabled=True,
    )

    result = await delete_skill_for_agent(
        user_id="u1", workspace_id=ws.id, skill_id=skill.id
    )
    assert result["ok"] is True
    assert await Skill.get(skill.id) is None


@pytest.mark.asyncio
async def test_create_workspace_skill_with_app_id_wires_app_contains(monkeypatch):
    from app.agentive.services.agent_skills import create_workspace_skill
    from app.models.edges import CONTAINS
    from app.models.nodes import Workspace

    ws = await Workspace.create(name="WS", kind="personal")
    app = await _make_app("Custom App", workspace_id=ws.id)

    skill = await create_workspace_skill(
        workspace_id=ws.id,
        user_id="u1",
        key="app_scoped_skill",
        name="App Scoped Skill",
        description="Does app-scoped things.",
        body_override="# SOP\n\nDo the thing.",
        tools_required=[],
        app_id=app.id,
    )

    assert skill.app_id == app.id
    assert skill.origin == "workspace"
    assert skill.private is True  # default when app_id given

    ctx = await app.get_context()
    edges = await ctx.find_edges_between(app.id, skill.id, edge_class=CONTAINS)
    assert edges, "expected App -CONTAINS-> Skill edge"

    ws_edges = await ctx.find_edges_between(ws.id, skill.id, edge_class=CONTAINS)
    assert not ws_edges, "should NOT also wire Workspace -CONTAINS-> Skill"


@pytest.mark.asyncio
async def test_create_workspace_skill_app_id_explicit_private_false():
    from app.agentive.services.agent_skills import create_workspace_skill
    from app.models.nodes import Workspace

    ws = await Workspace.create(name="WS2", kind="personal")
    app = await _make_app("App2", workspace_id=ws.id)

    skill = await create_workspace_skill(
        workspace_id=ws.id,
        user_id="u1",
        key="public_app_skill",
        name="Public App Skill",
        description="Visible workspace-wide despite being app-scoped.",
        body_override="# SOP",
        tools_required=[],
        app_id=app.id,
        private=False,
    )
    assert skill.private is False


@pytest.mark.asyncio
async def test_create_workspace_skill_app_id_wrong_workspace_rejected():
    from app.agentive.services.agent_skills import create_workspace_skill
    from app.api.errors import ResourceNotFoundError
    from app.models.nodes import Workspace

    ws_a = await Workspace.create(name="WS-A", kind="personal")
    ws_b = await Workspace.create(name="WS-B", kind="personal")
    app_in_b = await _make_app("App In B", workspace_id=ws_b.id)

    with pytest.raises(ResourceNotFoundError):
        await create_workspace_skill(
            workspace_id=ws_a.id,
            user_id="u1",
            key="cross_ws_skill",
            name="Cross WS",
            description="Should be rejected.",
            body_override="# SOP",
            tools_required=[],
            app_id=app_in_b.id,
        )


@pytest.mark.asyncio
async def test_author_skill_for_agent_threads_app_id_and_private(monkeypatch):
    from app.services import agent_skill_authoring

    monkeypatch.setattr(agent_skill_authoring, "is_workspace_admin_or_owner", _true)

    ws = await Workspace.create(name="WS4", kind="personal")
    app = await _make_app("App4", workspace_id=ws.id)

    result = await author_skill_for_agent(
        user_id="u1",
        workspace_id=ws.id,
        name="App Skill Via Agent",
        description="Created via the agent tool.",
        body_override="# SOP",
        app_id=app.id,
        private=False,
    )
    skill = await Skill.get(result["skill_id"])
    assert skill.app_id == app.id
    assert skill.private is False


@pytest.mark.asyncio
async def test_update_workspace_skill_can_flip_private():
    from app.agentive.services.agent_skills import (
        create_workspace_skill,
        update_workspace_skill,
    )
    from app.models.nodes import Workspace

    ws = await Workspace.create(name="WS3", kind="personal")
    app = await _make_app("App3", workspace_id=ws.id)
    skill = await create_workspace_skill(
        workspace_id=ws.id,
        user_id="u1",
        key="flip_me",
        name="Flip Me",
        description="Starts private.",
        body_override="# SOP",
        tools_required=[],
        app_id=app.id,
    )
    assert skill.private is True

    updated = await update_workspace_skill(
        skill, workspace_id=ws.id, user_id="u1", patch={"private": False}
    )
    assert updated.private is False


@pytest.mark.asyncio
async def test_list_workspace_skills_surfaces_app_for_app_scoped_workspace_skill():
    from app.agentive.services.agent_skills import (
        create_workspace_skill,
        list_workspace_skills,
    )

    # An App-scoped workspace skill defaults to private=True, and the editor
    # list now gates those on access to the owning App (a workspace member
    # without App access must not see an App-private SOP). Model a real
    # principal that owns the App, as production does — a bare synthetic id
    # has no User node, so get_user_accessible_apps() correctly returns [].
    from app.models.edges import OWNS
    from app.models.nodes import User, Workspace

    now = utc_now_iso()
    ws = await Workspace.create(name="WS-List", kind="personal")
    owner = await User.create(
        user_id="u1",
        display_name="Skill Author",
        created_at=now,
        updated_at=now,
    )
    app = await _make_app("Listed App", workspace_id=ws.id)
    await owner.connect(app, edge=OWNS, role="owner", granted_at=now)
    await create_workspace_skill(
        workspace_id=ws.id,
        user_id=owner.id,
        key="listed_skill",
        name="Listed Skill",
        description="Should show its app.",
        body_override="# SOP",
        tools_required=[],
        app_id=app.id,
    )

    rows = await list_workspace_skills(ws.id, user_id=owner.id)
    row = next(r for r in rows if r["key"] == "listed_skill")
    assert row["app_id"] == app.id
    assert row["app_slug"]  # non-empty now that app is passed through


@pytest.mark.asyncio
async def test_list_workspace_skills_plain_skill_has_empty_app_slug():
    """Regression guard: a plain (non-app-scoped) workspace skill must still
    get an empty app_slug/app_id after Task 7's app-surfacing change — the
    owning_app lookup must resolve to None, not accidentally pick up an
    unrelated app."""
    from app.agentive.services.agent_skills import (
        create_workspace_skill,
        list_workspace_skills,
    )
    from app.models.nodes import Workspace

    ws = await Workspace.create(name="WS-Plain", kind="personal")
    await create_workspace_skill(
        workspace_id=ws.id,
        user_id="u1",
        key="plain_skill",
        name="Plain Skill",
        description="No app scoping.",
        body_override="# SOP",
        tools_required=[],
    )

    rows = await list_workspace_skills(ws.id, user_id="u1")
    row = next(r for r in rows if r["key"] == "plain_skill")
    assert row["app_id"] == ""
    assert not row["app_slug"]
