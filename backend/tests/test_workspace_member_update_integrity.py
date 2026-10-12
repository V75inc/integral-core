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
    from app.models.nodes import User
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
    member = await User.get(member_id)
    assert member.user_id in seen, "the auth-principal cache key must also be evicted"


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
async def test_failed_in_place_update_preserves_membership(monkeypatch):
    """A primary update failure must leave the same current membership intact."""
    from jvspatial.core.context import get_default_context

    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.workspaces import patch_workspace_member

    owner_auth, ws_id, member_id = await _org_with_member()
    database = get_default_context().database
    from jvspatial.db.postgres import PostgresTransaction

    def install_failure(cls):
        original = cls.find_one_and_update

        async def fail_update(self, collection, query, update):
            if (
                collection == "edge"
                and query.get("entity") == "IS_MEMBER_OF"
                and update.get("$inc", {}).get("context.grant_revision") == 1
            ):
                raise RuntimeError("simulated membership update failure")
            return await original(self, collection, query, update)

        monkeypatch.setattr(cls, "find_one_and_update", fail_update)

    for cls in {type(database), PostgresTransaction}:
        install_failure(cls)
    with pytest.raises(Exception):
        await invoke_route_in_process(
            patch_workspace_member,
            principal_id=owner_auth,
            scope=ws_id,
            workspace_id=ws_id,
            member_user_id=member_id,
            role="admin",
        )
    assert (await _member_role(ws_id, member_id)) == ("member", 1)


@pytest.mark.asyncio
async def test_removed_member_cannot_use_resource_ownership_to_bypass_workspace_gate(
    authenticated_client, second_user_client, second_user, monkeypatch
):
    """A Track creator loses private access and public write access on revoke."""
    from app.services import permissions_process_cache

    permissions_process_cache.clear_all()
    monkeypatch.setattr(permissions_process_cache, "_ENABLED", True)
    created_ws = await authenticated_client.post(
        "/api/workspaces", json={"name": "Revocation Gate Org"}
    )
    assert created_ws.status_code == 200, created_ws.text
    ws_id = created_ws.json()["workspace"]["id"]
    added = await authenticated_client.post(
        f"/api/workspaces/{ws_id}/members",
        json={
            "member_user_id": second_user.id,
            "role": "member",
            "can_create_tracks": True,
        },
    )
    assert added.status_code == 200, added.text

    scoped = {"X-Integral-Scope": f"ws:{ws_id}"}
    private = await second_user_client.post(
        "/api/tracks",
        headers=scoped,
        json={"title": "Former member private", "visibility": "private"},
    )
    public = await second_user_client.post(
        "/api/tracks",
        headers=scoped,
        json={"title": "Former member public", "visibility": "public"},
    )
    assert private.status_code == public.status_code == 200
    private_id = private.json()["track"]["id"]
    public_id = public.json()["track"]["id"]
    assert (
        await second_user_client.get(f"/api/tracks/{private_id}")
    ).status_code == 200

    removed = await authenticated_client.delete(
        f"/api/workspaces/{ws_id}/members/{second_user.id}"
    )
    assert removed.status_code == 200, removed.text

    assert (
        await second_user_client.get(f"/api/tracks/{private_id}")
    ).status_code == 403
    dashboard = await second_user_client.get("/api/me/mission-control")
    assert dashboard.status_code == 200, dashboard.text
    assert private_id not in {t["id"] for t in dashboard.json()["tracks"]}
    denied_entry = await second_user_client.post(
        "/api/entries", json={"track_id": private_id, "title": "Denied"}
    )
    assert denied_entry.status_code == 403, denied_entry.text
    # Public visibility survives as a read grant, not the old OWNS write role.
    assert (await second_user_client.get(f"/api/tracks/{public_id}")).status_code == 200
    denied_public_write = await second_user_client.post(
        "/api/entries", json={"track_id": public_id, "title": "Denied"}
    )
    assert denied_public_write.status_code == 403, denied_public_write.text
    permissions_process_cache.clear_all()
