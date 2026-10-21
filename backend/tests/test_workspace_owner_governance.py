"""Workspace stewardship governs private data without inventing App role grants."""

import pytest

from app.models.edges import COLLABORATES_ON, EXCLUDED_FROM, IS_MEMBER_OF, OWNS
from app.models.nodes import User
from app.schemas.policy import Resource, Subject
from app.services.app_graph import catalog_user
from app.services.permissions import (
    can_admin_app,
    can_admin_track,
    can_edit_entry,
    can_view_entry,
    get_user_accessible_apps,
    get_user_accessible_tracks,
    resolve_role,
)
from app.services.policy_engine import evaluate
from app.services.sharing import list_access
from app.services.workspace_governance import resolve_owner_governance
from tests.test_resource_location import graph

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


async def private_member_graph():
    owner, workspace, app, track, entry = await graph()
    creator = await User.create(display_name="Resource steward")
    await catalog_user(creator)
    await creator.connect(workspace, edge=IS_MEMBER_OF, role="member")
    context = await owner.get_context()
    for node in (app, track):
        for edge in await context.find_edges_between(
            owner.id, node.id, edge_class=OWNS
        ):
            await edge.delete()
        await creator.connect(node, edge=OWNS)
        await owner.connect(node, edge=EXCLUDED_FROM)
    await owner.connect(entry, edge=EXCLUDED_FROM)
    return owner, creator, workspace, app, track, entry


async def test_owner_governs_private_descendants_despite_inherited_exclusions():
    owner, creator, workspace, app, track, entry = await private_member_graph()
    for kind, node in (("app", app), ("track", track), ("entry", entry)):
        assert await resolve_role(owner.id, kind, node.id) is None
        grant = await resolve_owner_governance(owner.id, kind, node.id, "resource.read")
        assert grant is not None
        assert grant.workspace_id == workspace.id and grant.source == "workspace_owner"
    assert await can_admin_app(owner.id, app.id)
    assert await can_admin_track(owner.id, track.id)
    assert await can_view_entry(owner.id, entry.id)
    assert await can_edit_entry(owner.id, entry.id)
    assert app.id in {node.id for node in await get_user_accessible_apps(owner.id)}
    assert track.id in {node.id for node in await get_user_accessible_tracks(owner.id)}
    context = await owner.get_context()
    assert not await context.find_edges_between(owner.id, app.id, edge_class=OWNS)
    assert not await context.find_edges_between(
        owner.id, app.id, edge_class=COLLABORATES_ON
    )
    assert await resolve_role(creator.id, "app", app.id) == "owner"


async def test_owner_can_review_and_manage_private_resource_access():
    owner, _, workspace, app, _, _ = await private_member_graph()
    snapshot = await list_access(owner.id, "app", app.id)
    assert snapshot["governance"] == {
        "source": "workspace_owner",
        "workspace_id": workspace.id,
    }
    assert snapshot["effective_role"] is None and snapshot["links_visible"] is True
    decision = await evaluate(
        subject=Subject(kind="human", id=owner.id),
        action="app.collaborator_add",
        resource=Resource(kind="app", id=app.id, scope=f"app:{app.id}"),
    )
    assert decision.allowed


async def test_admin_cannot_inherit_protected_owner_governance():
    _, creator, workspace, app, track, _ = await private_member_graph()
    admin = await User.create(display_name="Ordinary workspace admin")
    await catalog_user(admin)
    await admin.connect(workspace, edge=IS_MEMBER_OF, role="admin")
    assert (
        await resolve_owner_governance(admin.id, "app", app.id, "resource.configure")
        is None
    )
    assert not await can_admin_app(admin.id, app.id)
    await admin.connect(app, edge=COLLABORATES_ON, role="admin")
    assert await can_admin_app(admin.id, app.id)
    assert not await can_admin_track(admin.id, track.id)
    assert await resolve_role(creator.id, "track", track.id) == "owner"


async def test_governance_cannot_cross_workspace_or_invent_a_business_function():
    owner, _, _, app, _, _ = await private_member_graph()
    _, _, foreign, _, _ = await graph()
    assert (
        await resolve_owner_governance(owner.id, "app", foreign.id, "resource.read")
        is None
    )
    assert (
        await resolve_owner_governance(owner.id, "app", app.id, "workflow.approve")
        is None
    )
    decision = await evaluate(
        subject=Subject(kind="human", id=owner.id),
        action="workflow.approve",
        resource=Resource(kind="app", id=app.id, scope=f"app:{app.id}"),
    )
    assert not decision.allowed


@pytest.mark.parametrize("principal_form", ["graph", "auth"])
async def test_protected_owner_governance_requires_current_linked_identity(
    principal_form,
):
    from jvspatial.api.auth.models import User as AuthUser

    owner, _, _, app, _, _ = await private_member_graph()
    identity = await AuthUser.create(
        email="governance-identity@example.com", password_hash="fixture"
    )
    owner.user_id = identity.id
    await owner.save()
    principal = owner.id if principal_form == "graph" else identity.id
    assert (
        await resolve_owner_governance(principal, "app", app.id, "resource.read")
        is not None
    )
    context = await owner.get_context()
    await context.database.find_one_and_update(
        "object", {"id": identity.id}, {"$set": {"context.is_active": False}}
    )
    assert (
        await resolve_owner_governance(principal, "app", app.id, "resource.read")
        is None
    )
    assert not await can_admin_app(principal, app.id)


async def test_protected_owner_can_toggle_reactions_without_an_ordinary_grant():
    from fastapi import Request
    from jvspatial.api.auth.models import User as AuthUser

    from app.api.entries import add_reaction

    owner, _, _, _, _, entry = await private_member_graph()
    identity = await AuthUser.create(
        email="owner-reaction@example.com", password_hash="fixture"
    )
    owner.user_id = identity.id
    await owner.save()
    assert await resolve_role(identity.id, "entry", entry.id) is None
    request = Request(
        {"type": "http", "headers": [], "state": {"user": {"id": identity.id}}}
    )
    result = await add_reaction(request, entry.id, "thumbs-up")
    assert identity.id in result["reactions"]["thumbs-up"]


@pytest.mark.parametrize(
    "state",
    ["active", "paused", "installing", "awaiting_settings", "failed", "uninstalled"],
)
async def test_lifecycle_data_recovery_and_execution_are_distinct(state):
    from app.api.errors import BadRequestError
    from app.services.app_operations.dispatch import _ensure_app_active
    from app.services.resource_location import locate_resource, locate_resources

    owner, creator, _, app, track, entry = await private_member_graph()
    app.lifecycle_state = state
    await app.save()
    data_allowed = state in {"active", "paused"}
    recovery_allowed = state != "uninstalled"
    assert (
        await resolve_role(creator.id, "entry", entry.id) is not None
    ) == data_allowed
    assert (await can_view_entry(owner.id, entry.id)) == data_allowed
    assert (
        app.id in {n.id for n in await get_user_accessible_apps(owner.id)}
    ) == data_allowed
    assert (
        track.id in {n.id for n in await get_user_accessible_tracks(owner.id)}
    ) == data_allowed
    assert (await can_admin_app(owner.id, app.id)) == recovery_allowed
    assert (await can_admin_app(creator.id, app.id)) == recovery_allowed
    assert (await can_admin_track(creator.id, track.id)) == recovery_allowed
    assert (await locate_resource("entry", entry.id) is not None) == (state == "active")
    assert ((await locate_resources("entry", [entry.id]))[entry.id] is not None) == (
        state == "active"
    )
    if state == "paused":
        with pytest.raises(BadRequestError, match="not active"):
            await _ensure_app_active(app, owner.id)


async def test_paused_app_http_resume_is_available_to_workspace_owner(
    authenticated_client,
    test_user,
):
    from app.agentive.work_models import WorkItem
    from app.services.app_service import create_app_for_user

    owner, creator, workspace, _, _, _ = await private_member_graph()
    context = await owner.get_context()
    for edge in await context.find_edges_between(
        owner.id, workspace.id, edge_class=IS_MEMBER_OF
    ):
        await edge.delete()
    await test_user.connect(workspace, edge=IS_MEMBER_OF, role="owner")
    # Production App creation supplies the active definition required by
    # durable lifecycle work, rather than a definition-less graph stub.
    app = await create_app_for_user(
        test_user.id, "Recovery admission", workspace_id=workspace.id
    )
    for edge in await context.find_edges_between(test_user.id, app.id, edge_class=OWNS):
        await edge.delete()
    await creator.connect(app, edge=OWNS)
    await test_user.connect(app, edge=EXCLUDED_FROM)
    app.lifecycle_state = "paused"
    await app.save()
    assert await resolve_role(test_user.id, "app", app.id) is None
    response = await authenticated_client.post(f"/api/apps/{app.id}/resume")
    assert response.status_code == 200, response.text
    work = await WorkItem.find_one({"work_item_id": response.json()["work_item_id"]})
    assert work is not None and work.app_id == app.id
    assert work.input_payload["action"] == "resume"
    # Enqueueing recovery does not itself activate the App or grant execution.
    from app.models.nodes import App
    from app.services.resource_location import locate_resource

    assert (await App.find_one({"id": app.id})).lifecycle_state == "paused"
    assert await locate_resource("app", app.id) is None


@pytest.mark.parametrize("origin", ["app_operation", "app_query", "app_declaration"])
async def test_owner_governance_is_not_business_function_authority(origin):
    from app.contracts.runtime import ExecutionScope
    from app.modules.policy import policy_module

    owner, creator, _, app, _, _ = await private_member_graph()
    context = await owner.get_context()
    # Workspace visibility supplies an ordinary commenter floor. The owner
    # must not combine that floor with protected write governance to execute.
    for edge in await context.find_edges_between(
        owner.id, app.id, edge_class=EXCLUDED_FROM
    ):
        await edge.delete()
    app.visibility = "workspace"
    await app.save()
    assert await resolve_role(owner.id, "app", app.id) == "commenter"
    resource = Resource(kind="app", id=app.id, scope=f"app:{app.id}")
    assert (
        await evaluate(
            subject=Subject(kind="human", id=owner.id),
            action="entry.create",
            resource=resource,
        )
    ).allowed
    scope = ExecutionScope.create(
        principal_id=owner.id, workspace_id=app.workspace_id, origin=origin
    )
    assert not (
        await policy_module.evaluate(
            scope=scope, action="entry.create", resource=resource
        )
    ).allowed
    await owner.connect(app, edge=COLLABORATES_ON, role="editor")
    assert (
        await policy_module.evaluate(
            scope=scope, action="entry.create", resource=resource
        )
    ).allowed
    for edge in await context.find_edges_between(
        owner.id, app.id, edge_class=COLLABORATES_ON
    ):
        await edge.delete()
    assert not (
        await policy_module.evaluate(
            scope=scope, action="entry.create", resource=resource
        )
    ).allowed
    # Ordinary function evaluation must restore the generic governance mode.
    assert (
        await evaluate(
            subject=Subject(kind="human", id=owner.id),
            action="entry.create",
            resource=resource,
        )
    ).allowed
    assert await resolve_role(creator.id, "app", app.id) == "owner"


async def test_function_read_cannot_reuse_a_cached_governance_read():
    from app.contracts.runtime import ExecutionScope
    from app.middleware.permissions_cache import reset_permissions_cache
    from app.modules.policy import policy_module

    owner, _, _, app, _, _ = await private_member_graph()
    resource = Resource(kind="app", id=app.id, scope=f"app:{app.id}")
    reset_permissions_cache()
    assert (
        await evaluate(
            subject=Subject(kind="human", id=owner.id),
            action="app.read",
            resource=resource,
        )
    ).allowed
    scope = ExecutionScope.create(
        principal_id=owner.id, workspace_id=app.workspace_id, origin="app_declaration"
    )
    assert not (
        await policy_module.evaluate(scope=scope, action="app.read", resource=resource)
    ).allowed
    assert (
        await evaluate(
            subject=Subject(kind="human", id=owner.id),
            action="app.read",
            resource=resource,
        )
    ).allowed


async def test_skill_discovery_requires_active_ordinary_app_authority(tmp_path):
    from app.agentive.services.skill_registry import get_callable_skills, register_skill
    from app.agentive.workspace_agent_profile import compose_workspace_agent_profile
    from app.services.agent_skill_authoring import author_skill_for_agent

    owner, creator, workspace, app, _, entry = await private_member_graph()
    skill_dir = tmp_path / "skills" / "governance-boundary"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: governance-boundary\ndescription: A delegated App function.\n---\nUse the declared function.\n"
    )
    app.metadata = {"bundle_dir_path": str(tmp_path)}
    await app.save()
    skill = await register_skill(
        app_id=app.id,
        workspace_id=workspace.id,
        skill_spec={
            "key": "governance-boundary",
            "kind": "declarative",
            "prompt_template_ref": "skills/governance-boundary/SKILL.md",
        },
    )
    authored = await author_skill_for_agent(
        user_id=owner.id,
        workspace_id=workspace.id,
        app_id=app.id,
        key="workspace_governance_boundary",
        name="Workspace function",
        description="App scoped workspace function",
        body_override="Perform the delegated function.",
    )
    assert skill.id in {
        s.id for s in await get_callable_skills("", workspace.id, user_id=creator.id)
    }
    assert (
        await get_callable_skills(
            "", workspace.id, user_id=owner.id, include_private=True
        )
        == []
    )
    profile = await compose_workspace_agent_profile(workspace.id, user_id=owner.id)
    assert not any(
        (
            "governance-boundary" in doc.name
            or "workspace_governance_boundary" in doc.name
        )
        for doc in profile.overlay_skill_docs
    )
    await owner.connect(app, edge=COLLABORATES_ON, role="viewer")
    assert skill.id in {
        s.id for s in await get_callable_skills("", workspace.id, user_id=owner.id)
    }
    profile = await compose_workspace_agent_profile(workspace.id, user_id=owner.id)
    assert any(
        (
            "governance-boundary" in doc.name
            or "workspace_governance_boundary" in doc.name
        )
        for doc in profile.overlay_skill_docs
    )
    app.lifecycle_state = "paused"
    await app.save()
    assert await can_view_entry(owner.id, entry.id)
    assert await get_callable_skills("", workspace.id, user_id=owner.id) == []
    profile = await compose_workspace_agent_profile(workspace.id, user_id=owner.id)
    assert not any(
        (
            "governance-boundary" in doc.name
            or "workspace_governance_boundary" in doc.name
        )
        for doc in profile.overlay_skill_docs
    )
    assert authored["skill_id"]
