"""``patch_workspace_member`` must not evict the member or serve a stale role.

Two defects in one handler:

1. It changes a role by deleting every ``IS_MEMBER_OF`` edge and creating a
   replacement. There is no cross-entity transaction, so a failure between the
   two steps left the member with no membership edge at all -- silently evicted
   from the workspace by what was meant to be a role tweak.

2. It never invalidated the per-user permission cache. A demotion
   (admin -> guest) kept serving the old role for up to
   ``PERMISSION_PROCESS_CACHE_TTL`` seconds. ``remove_workspace_member``
   already invalidated; this path did not.
"""

import pytest

# Part of the per-PR smoke gate (see pyproject [tool.pytest.ini_options] markers).
# CI bills only this subset; the full suite runs locally via `make verify` and nightly.
pytestmark = pytest.mark.smoke


async def _org_with_member():
    """Owner + org workspace + one 'member'-role member. Returns ids."""
    from jvspatial.api.auth.models import UserCreate

    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.auth import _get_auth_service
    from app.api.workspaces import add_workspace_member, create_workspace
    from app.models.nodes import User
    from app.services.app_graph import catalog_user

    auth_service = _get_auth_service()

    async def mk(email, name):
        resp = await auth_service.register_user(
            UserCreate(email=email, password="testpassword123")
        )
        node = await User.create(user_id=resp.id, display_name=name)
        await catalog_user(node)
        return resp.id, node

    owner_auth, _owner_node = await mk("wsmember-owner@example.com", "Owner")
    _member_auth, member_node = await mk("wsmember-member@example.com", "Member")

    ws = await invoke_route_in_process(
        create_workspace,
        principal_id=owner_auth,
        name="Member Integrity Org",
        workspace_type="collaborative",
    )
    ws_id = ws["workspace"]["id"]

    await invoke_route_in_process(
        add_workspace_member,
        principal_id=owner_auth,
        scope=ws_id,
        workspace_id=ws_id,
        member_user_id=member_node.id,
        role="member",
    )
    return owner_auth, ws_id, member_node.id


async def _member_role(workspace_id: str, member_id: str):
    """Read the role straight off the IS_MEMBER_OF edge."""
    from app.models.edges import IS_MEMBER_OF
    from app.models.nodes import User, Workspace

    member = await User.get(member_id)
    ws = await Workspace.get(workspace_id)
    ctx = await member.get_context()
    edges = await ctx.find_edges_between(
        source_id=member.id, target_id=ws.id, edge_class=IS_MEMBER_OF
    )
    if not edges:
        return None
    e = edges[0]
    role = getattr(e, "role", None)
    if role is None:
        rctx = getattr(e, "context", None)
        role = rctx.get("role") if isinstance(rctx, dict) else None
    return role, len(edges)


@pytest.mark.asyncio
async def test_role_update_invalidates_the_permission_cache(monkeypatch):
    """A demotion must not keep serving the old role from cache."""
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.workspaces import patch_workspace_member
    from app.services import permissions_process_cache

    owner_auth, ws_id, member_id = await _org_with_member()

    seen = []
    monkeypatch.setattr(
        permissions_process_cache, "invalidate_user", lambda uid: seen.append(uid)
    )

    await invoke_route_in_process(
        patch_workspace_member,
        principal_id=owner_auth,
        scope=ws_id,
        workspace_id=ws_id,
        member_user_id=member_id,
        role="guest",
    )

    assert member_id in seen, (
        "patch_workspace_member did not invalidate the demoted member's "
        "permission cache; the old role keeps resolving until the TTL expires"
    )


@pytest.mark.asyncio
async def test_role_update_applies_and_leaves_exactly_one_edge():
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.workspaces import patch_workspace_member

    owner_auth, ws_id, member_id = await _org_with_member()
    assert (await _member_role(ws_id, member_id))[0] == "member"

    await invoke_route_in_process(
        patch_workspace_member,
        principal_id=owner_auth,
        scope=ws_id,
        workspace_id=ws_id,
        member_user_id=member_id,
        role="admin",
    )

    role, edge_count = await _member_role(ws_id, member_id)
    assert role == "admin"
    assert edge_count == 1, "role update left duplicate IS_MEMBER_OF edges"


@pytest.mark.asyncio
async def test_failed_swap_restores_membership_instead_of_evicting(monkeypatch):
    """The partial-write case: connect() fails after the delete.

    Previously the member was left with no membership edge -- locked out of the
    workspace. The handler must put the prior membership back and surface the
    failure rather than silently evicting.
    """
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.workspaces import patch_workspace_member
    from app.models import nodes as nodes_mod

    owner_auth, ws_id, member_id = await _org_with_member()
    assert (await _member_role(ws_id, member_id))[0] == "member"

    original_connect = nodes_mod.User.connect
    calls = {"n": 0}

    async def flaky_connect(self, *args, **kwargs):
        # Fail the first connect (the new edge); allow the rollback through.
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated write failure mid-swap")
        return await original_connect(self, *args, **kwargs)

    monkeypatch.setattr(nodes_mod.User, "connect", flaky_connect, raising=False)

    with pytest.raises(Exception):
        await invoke_route_in_process(
            patch_workspace_member,
            principal_id=owner_auth,
            scope=ws_id,
            workspace_id=ws_id,
            member_user_id=member_id,
            role="admin",
        )

    monkeypatch.undo()

    restored = await _member_role(ws_id, member_id)
    assert restored is not None, (
        "a failed role update evicted the member from the workspace: no "
        "IS_MEMBER_OF edge remains"
    )
    assert restored[0] == "member", "rollback restored the wrong role"
