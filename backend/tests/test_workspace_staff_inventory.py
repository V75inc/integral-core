"""Workspace owner/admin inventory parity for apps and tracks."""

from datetime import datetime

import pytest

from app.models.edges import CONTAINS, IS_MEMBER_OF
from app.models.nodes import App, Track, User, Workspace
from app.services.app_graph import (
    catalog_app,
    catalog_track,
    catalog_workspace,
    ensure_integral_app_graph,
    wire_app_owner,
)
from app.services.permissions import (
    can_admin_track,
    get_user_accessible_apps,
    get_user_accessible_tracks,
    resolve_role,
)
from app.services.workspace_permissions import collect_org_workspace_staff_inventory


async def _org_workspace_with_inventory(
    *, owner: User
) -> tuple[Workspace, App, Track, Track]:
    """Org workspace with one app, one app-nested track, one standalone track."""
    now = datetime.now().isoformat()
    ws = await Workspace.create(
        kind="organization",
        name="Staff Inventory Org",
        name_fold="staff inventory org",
        created_at=now,
        updated_at=now,
    )
    await catalog_workspace(ws)
    await owner.connect(
        ws,
        edge=IS_MEMBER_OF,
        role="owner",
        joined_at=now,
        can_create_apps=True,
        can_create_tracks=True,
    )

    app = await App.create(
        name="Bundle App",
        name_fold="bundle app",
        owner_user_id=owner.id,
        workspace_id=ws.id,
        lifecycle_state="active",
        created_at=now,
        updated_at=now,
    )
    await catalog_app(app)
    await wire_app_owner(app, owner.id, workspace_id=ws.id)

    nested = await Track.create(
        title="Nested Track",
        title_fold="nested track",
        owner_id=owner.id,
        workspace_id=ws.id,
        created_at=now,
        updated_at=now,
    )
    await app.connect(nested, edge=CONTAINS, added_at=now)

    standalone = await Track.create(
        title="Standalone Track",
        title_fold="standalone track",
        owner_id=owner.id,
        workspace_id=ws.id,
        created_at=now,
        updated_at=now,
    )
    await catalog_track(standalone)

    return ws, app, nested, standalone


@pytest.mark.asyncio
async def test_collect_org_workspace_staff_inventory_includes_apps_and_tracks():
    await ensure_integral_app_graph(include_library=False)
    owner = await User.create(user_id="staff_owner", display_name="Owner")
    ws, app, nested, standalone = await _org_workspace_with_inventory(owner=owner)

    apps, tracks = await collect_org_workspace_staff_inventory(ws)
    assert {a.id for a in apps} == {app.id}
    assert {t.id for t in tracks} == {nested.id, standalone.id}


@pytest.mark.asyncio
async def test_workspace_admin_sees_full_inventory_without_collaborator_edges():
    await ensure_integral_app_graph(include_library=False)
    owner = await User.create(user_id="staff_owner2", display_name="Owner")
    ws, app, nested, standalone = await _org_workspace_with_inventory(owner=owner)

    admin = await User.create(user_id="staff_admin", display_name="Admin")
    await admin.connect(
        ws,
        edge=IS_MEMBER_OF,
        role="admin",
        joined_at=datetime.now().isoformat(),
        can_create_apps=True,
        can_create_tracks=True,
    )

    admin_apps = await get_user_accessible_apps(admin.id)
    admin_tracks = await get_user_accessible_tracks(admin.id)

    assert {a.id for a in admin_apps} == {app.id}
    assert {t.id for t in admin_tracks} == {nested.id, standalone.id}


@pytest.mark.asyncio
async def test_workspace_member_without_grants_sees_no_private_inventory():
    """Member sees nothing when all apps/tracks remain private (default fixture)."""
    await ensure_integral_app_graph(include_library=False)
    owner = await User.create(user_id="staff_owner3", display_name="Owner")
    ws, _app, _nested, _standalone = await _org_workspace_with_inventory(owner=owner)

    member = await User.create(user_id="staff_member", display_name="Member")
    await member.connect(
        ws,
        edge=IS_MEMBER_OF,
        role="member",
        joined_at=datetime.now().isoformat(),
        can_create_apps=False,
        can_create_tracks=False,
    )

    member_apps = await get_user_accessible_apps(member.id)
    member_tracks = await get_user_accessible_tracks(member.id)

    assert member_apps == []
    assert member_tracks == []


@pytest.mark.asyncio
async def test_staff_inventory_includes_apps_missing_branch_catalog_edge():
    """Apps with workspace_id but no CATALOGS branch edge still surface for staff."""
    await ensure_integral_app_graph(include_library=False)
    now = datetime.now().isoformat()
    owner = await User.create(user_id="staff_owner4", display_name="Owner")
    ws = await Workspace.create(
        kind="organization",
        name="Uncatalogued App Org",
        name_fold="uncatalogued app org",
        created_at=now,
        updated_at=now,
    )
    await catalog_workspace(ws)
    await owner.connect(
        ws,
        edge=IS_MEMBER_OF,
        role="owner",
        joined_at=now,
    )

    app = await App.create(
        name="Legacy App",
        name_fold="legacy app",
        owner_user_id=owner.id,
        workspace_id=ws.id,
        lifecycle_state="active",
        created_at=now,
        updated_at=now,
    )
    standalone = await Track.create(
        title="Legacy Standalone",
        title_fold="legacy standalone",
        owner_id=owner.id,
        workspace_id=ws.id,
        created_at=now,
        updated_at=now,
    )
    await catalog_track(standalone)

    admin = await User.create(user_id="staff_admin4", display_name="Admin")
    await admin.connect(
        ws,
        edge=IS_MEMBER_OF,
        role="admin",
        joined_at=now,
    )

    apps, tracks = await collect_org_workspace_staff_inventory(ws)
    assert {a.id for a in apps} == {app.id}
    assert {t.id for t in tracks} == {standalone.id}

    admin_apps = await get_user_accessible_apps(admin.id)
    assert {a.id for a in admin_apps} == {app.id}


@pytest.mark.asyncio
async def test_workspace_admin_implicit_role_is_commenter_on_inventory_resources():
    """Org workspace admin sees inventory but cannot manage it without a direct grant.

    Wave 1 narrowed ``workspace_staff_implicit_resource_role`` from "admin" to
    "viewer": org staff get inventory access implicitly, but minting shares or
    managing collaborators requires a direct owner/admin grant on the resource
    itself. See tests/test_wave1_access.py, which pins the contract from the
    other side.

    The implicit role is now ``commenter`` rather than ``viewer`` — staff may
    read and participate, which is what "administers this workspace" is taken
    to mean, while every authority gate stays exactly where Wave 1 put it. The
    assertions below are the point: participate yes, administer no.
    """
    await ensure_integral_app_graph(include_library=False)
    owner = await User.create(user_id="staff_owner5", display_name="Owner")
    ws, app, nested, standalone = await _org_workspace_with_inventory(owner=owner)

    admin = await User.create(user_id="staff_admin5", display_name="Admin")
    await admin.connect(
        ws,
        edge=IS_MEMBER_OF,
        role="admin",
        joined_at=datetime.now().isoformat(),
        can_create_apps=True,
        can_create_tracks=True,
    )

    # Participation, not authority.
    assert await resolve_role(admin.id, "app", app.id) == "commenter"
    assert await resolve_role(admin.id, "track", nested.id) == "commenter"
    assert await resolve_role(admin.id, "track", standalone.id) == "commenter"
    # ...and the management gate is now closed without a direct grant.
    assert await can_admin_track(admin.id, nested.id) is False
    assert await can_admin_track(admin.id, standalone.id) is False
