"""An App must never reach a state where nobody can administer it.

Track creation, content-profile mutation and collaborator management all gate
on ``app.update`` → ``can_admin_app`` → ``resolve_role(...) in ("owner",
"admin")``. An org workspace owner resolves only to the implicit staff
``viewer`` (inventory visibility by design), so an App with no ``OWNS`` edge is
administrable by *no* human and no UI surface can repair it.

``wire_app_owner`` used to return silently when the actor did not resolve to a
graph profile, which is exactly how that state was reached. Observed in a dev
workspace: an active App with zero ``OWNS`` edges whose ``owner_user_id`` still
named a principal — the field claimed an owner the graph did not have.
"""

from datetime import datetime

import pytest

from app.models.edges import IS_MEMBER_OF, OWNS
from app.models.nodes import App, User, Workspace
from app.services.app_graph import (
    AppOwnerWireError,
    catalog_workspace,
    ensure_integral_app_graph,
    wire_app_owner,
)
from app.services.permissions import can_admin_app, resolve_role


async def _org_workspace(owner: User | None) -> Workspace:
    now = datetime.now().isoformat()
    ws = await Workspace.create(
        kind="organization",
        name="Owner Grant Org",
        name_fold="owner grant org",
        created_at=now,
        updated_at=now,
    )
    await catalog_workspace(ws)
    if owner is not None:
        await owner.connect(
            ws,
            edge=IS_MEMBER_OF,
            role="owner",
            joined_at=now,
            can_create_apps=True,
            can_create_tracks=True,
        )
    return ws


async def _bare_app(ws: Workspace) -> App:
    """An App with no owner wired yet — the state wire_app_owner resolves."""
    now = datetime.now().isoformat()
    return await App.create(
        name="Grant Test App",
        name_fold="grant test app",
        workspace_id=ws.id,
        visibility="private",
        created_at=now,
        updated_at=now,
    )


async def _owns_edge_exists(user: User, app: App) -> bool:
    ctx = await user.get_context()
    return bool(await ctx.find_edges_between(user.id, app.id, edge_class=OWNS))


@pytest.mark.asyncio
async def test_wires_the_actor_as_owner():
    await ensure_integral_app_graph(include_library=False)
    actor = await User.create(name="Actor")
    ws = await _org_workspace(actor)
    app = await _bare_app(ws)

    await wire_app_owner(app, actor.id, workspace_id=ws.id)

    assert await _owns_edge_exists(actor, app)
    assert await resolve_role(actor.id, "app", app.id) == "owner"
    assert await can_admin_app(actor.id, app.id)


@pytest.mark.asyncio
async def test_falls_back_to_the_workspace_owner_when_the_actor_is_unknown():
    # The bug: an unresolvable actor (an `o.User.*` principal with no graph
    # profile, or an empty actor on a system-initiated install) skipped the
    # wire entirely and left the App unadministrable. The workspace owner is
    # the right fallback — they are the one otherwise locked out of an App in
    # their own workspace.
    await ensure_integral_app_graph(include_library=False)
    ws_owner = await User.create(name="Workspace Owner")
    ws = await _org_workspace(ws_owner)
    app = await _bare_app(ws)

    await wire_app_owner(app, "o.User.doesnotexist", workspace_id=ws.id)

    assert await _owns_edge_exists(ws_owner, app)
    assert await can_admin_app(ws_owner.id, app.id)


@pytest.mark.asyncio
async def test_raises_rather_than_leaving_an_app_nobody_can_administer():
    # No actor, no workspace owner: there is no one to grant to. Failing loudly
    # is recoverable (the install rolls back); committing an ownerless App is
    # not — no human resolves above the implicit staff `viewer` afterwards.
    await ensure_integral_app_graph(include_library=False)
    ws = await _org_workspace(None)
    app = await _bare_app(ws)

    with pytest.raises(AppOwnerWireError):
        await wire_app_owner(app, "", workspace_id=ws.id)


@pytest.mark.asyncio
async def test_owner_user_id_records_the_graph_id_not_the_principal_id():
    # `resolve_role` reads edges, which are keyed by the `n.User.*` node.
    # Stamping an `o.User.*` principal here made the field disagree with the
    # graph — it named an owner that held no grant, which is how the broken
    # App looked "owned" while being administrable by nobody.
    await ensure_integral_app_graph(include_library=False)
    ws_owner = await User.create(name="Graph Id Owner")
    ws = await _org_workspace(ws_owner)
    app = await _bare_app(ws)

    await wire_app_owner(app, "o.User.unresolvable", workspace_id=ws.id)

    refreshed = await App.get(app.id)
    assert refreshed is not None
    assert refreshed.owner_user_id == ws_owner.id
    assert not str(refreshed.owner_user_id).startswith("o.User.")


@pytest.mark.asyncio
async def test_is_idempotent_and_does_not_duplicate_the_grant():
    await ensure_integral_app_graph(include_library=False)
    actor = await User.create(name="Repeat Actor")
    ws = await _org_workspace(actor)
    app = await _bare_app(ws)

    await wire_app_owner(app, actor.id, workspace_id=ws.id)
    await wire_app_owner(app, actor.id, workspace_id=ws.id)

    ctx = await actor.get_context()
    edges = await ctx.find_edges_between(actor.id, app.id, edge_class=OWNS)
    assert len(edges) == 1


@pytest.mark.asyncio
async def test_workspace_owner_alone_cannot_administer_an_ownerless_app():
    # Pins the reason the missing grant is fatal rather than cosmetic: org
    # staff get the implicit participation role and nothing more, so without
    # the edge the 403 is unavoidable. If this ever starts resolving as
    # owner/admin, the fallback above is redundant and should be revisited.
    #
    # The implicit role is `commenter` (raised from `viewer` so workspace staff
    # can answer comments on content they administer — ARCHITECTURE §9.5). The
    # load-bearing assertion here is the second one: still not an admin.
    await ensure_integral_app_graph(include_library=False)
    ws_owner = await User.create(name="Unwired Owner")
    ws = await _org_workspace(ws_owner)
    app = await _bare_app(ws)

    assert await resolve_role(ws_owner.id, "app", app.id) == "commenter"
    assert not await can_admin_app(ws_owner.id, app.id)
