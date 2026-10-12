"""Workspace authority requires complete, current and unambiguous facts."""

import pytest

from app.exceptions import AuthorizationUnavailableError
from app.middleware.permissions_cache import isolated_permissions_cache
from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
from app.models.nodes import User
from app.services.app_graph import catalog_user
from app.services.permissions import resolve_role
from app.services.workspace_permissions import (
    can_access_workspace,
    get_workspace_owner_user_id,
    list_accessible_workspaces,
)
from tests.test_resource_location import graph

pytestmark = pytest.mark.smoke


@pytest.mark.asyncio
async def test_multiple_workspace_owners_never_selects_the_first():
    owner, workspace, _, _, _ = await graph()
    assert await get_workspace_owner_user_id(workspace.id) == owner.id
    assert await can_access_workspace(owner.id, workspace.id) == "owner"
    other = await User.create(display_name="Ambiguous owner")
    await catalog_user(other)
    await other.connect(workspace, edge=IS_MEMBER_OF, role="owner")
    assert await get_workspace_owner_user_id(workspace.id) is None
    for user in (owner, other):
        assert await can_access_workspace(user.id, workspace.id) == "none"
        with isolated_permissions_cache():
            assert await resolve_role(user.id, "workspace", workspace.id) is None


@pytest.mark.asyncio
async def test_owner_lookup_failure_is_unavailable(monkeypatch):
    owner, workspace, _, _, _ = await graph()
    context = await workspace.get_context()
    real_find = context.database.find

    async def fault(collection, query=None, *args, **kwargs):
        if (
            collection == "edge"
            and query
            and query.get("entity") == IS_MEMBER_OF.__name__
        ):
            raise RuntimeError("injected owner inventory failure")
        return await real_find(collection, query, *args, **kwargs)

    monkeypatch.setattr(context.database, "find", fault)
    with pytest.raises(AuthorizationUnavailableError):
        await get_workspace_owner_user_id(workspace.id)
    with pytest.raises(AuthorizationUnavailableError):
        await can_access_workspace(owner.id, workspace.id)


@pytest.mark.asyncio
async def test_workspace_inventory_cannot_return_partial_success(monkeypatch):
    owner, workspace, _, _, _ = await graph()
    assert [row.id for row in await list_accessible_workspaces(owner.id)] == [
        workspace.id
    ]
    calls = []

    async def fail_second_page(self, **kwargs):
        calls.append(kwargs.get("cursor"))
        if kwargs.get("cursor") is None:
            return [workspace], "next-page"
        raise RuntimeError("injected later membership page failure")

    monkeypatch.setattr(User, "nodes_page", fail_second_page)
    with pytest.raises(AuthorizationUnavailableError):
        await list_accessible_workspaces(owner.id)
    assert calls == [None, "next-page"]


@pytest.mark.asyncio
async def test_workspace_helpers_recheck_disabled_identity_after_warming():
    from jvspatial.api.auth.models import User as AuthUser

    _, workspace, _, _, _ = await graph()
    identity = await AuthUser.create(
        email="authority-facts@example.com", password_hash="fixture"
    )
    member = await User.create(display_name="Warm member", user_id=identity.id)
    await catalog_user(member)
    await member.connect(workspace, edge=IS_MEMBER_OF, role="member")
    assert await can_access_workspace(member.id, workspace.id) == "member"
    assert [row.id for row in await list_accessible_workspaces(member.id)] == [
        workspace.id
    ]
    identity.is_active = False
    await identity.save()
    assert await can_access_workspace(member.id, workspace.id) == "none"
    assert await list_accessible_workspaces(member.id) == []


@pytest.mark.asyncio
async def test_unknown_collaborator_role_is_not_downgraded_into_read_access():
    _, workspace, app, _, _ = await graph()
    reader = await User.create(display_name="Invalid role")
    await catalog_user(reader)
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    grant = await reader.connect(app, edge=COLLABORATES_ON, role="viewer")
    with isolated_permissions_cache():
        assert await resolve_role(reader.id, "app", app.id) == "viewer"
    context = await reader.get_context()
    await context.database.find_one_and_update(
        "edge",
        {"id": grant.id},
        {"$set": {"context.role": "unknown-access-vocabulary"}},
    )
    with isolated_permissions_cache(), pytest.raises(AuthorizationUnavailableError):
        await resolve_role(reader.id, "app", app.id)
