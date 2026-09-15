"""Visibility-based workspace member access (ARCHITECTURE §9.5 steps 3–4)."""

from datetime import datetime

import pytest
from httpx import AsyncClient

from app.models.edges import COLLABORATES_ON, CONTAINS, EXCLUDED_FROM, IS_MEMBER_OF
from app.models.nodes import App, Track, User, Workspace
from app.services.app_graph import (
    catalog_app,
    catalog_track,
    catalog_user,
    catalog_workspace,
    ensure_integral_app_graph,
    wire_app_owner,
)
from app.services.permissions import (
    get_user_accessible_apps,
    get_user_accessible_tracks,
    resolve_role,
)
from app.services.sharing import add_collaborator

# Part of the per-PR smoke gate (see pyproject [tool.pytest.ini_options] markers).
# CI bills only this subset; the full suite runs locally via `make verify` and nightly.
pytestmark = pytest.mark.smoke


async def _org_fixture(
    *,
    owner: User,
    app_visibility: str = "private",
    nested_track_visibility: str = "inherit",
    standalone_track_visibility: str = "private",
) -> tuple[Workspace, App, Track, Track]:
    now = datetime.now().isoformat()
    ws = await Workspace.create(
        kind="organization",
        name="Visibility Cascade Org",
        name_fold="visibility cascade org",
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
        name="Visibility App",
        name_fold="visibility app",
        owner_user_id=owner.id,
        workspace_id=ws.id,
        visibility=app_visibility,
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
        visibility=nested_track_visibility,
        created_at=now,
        updated_at=now,
    )
    await app.connect(nested, edge=CONTAINS, added_at=now)

    standalone = await Track.create(
        title="Standalone Track",
        title_fold="standalone track",
        owner_id=owner.id,
        workspace_id=ws.id,
        visibility=standalone_track_visibility,
        created_at=now,
        updated_at=now,
    )
    await catalog_track(standalone)

    return ws, app, nested, standalone


async def _ws_member(ws: Workspace, user: User, role: str = "member") -> None:
    now = datetime.now().isoformat()
    await user.connect(
        ws,
        edge=IS_MEMBER_OF,
        role=role,
        joined_at=now,
        can_create_apps=False,
        can_create_tracks=False,
    )


@pytest.mark.asyncio
async def test_member_sees_workspace_visible_app_without_collaborator_edge():
    await ensure_integral_app_graph(include_library=False)
    owner = await User.create(user_id="vis_owner1", display_name="Owner")
    ws, app, _nested, _standalone = await _org_fixture(
        owner=owner, app_visibility="workspace"
    )
    member = await User.create(user_id="vis_member1", display_name="Member")
    await _ws_member(ws, member)

    assert await resolve_role(member.id, "app", app.id) == "viewer"
    assert app.id in {a.id for a in await get_user_accessible_apps(member.id)}


@pytest.mark.asyncio
async def test_member_cannot_see_private_app():
    await ensure_integral_app_graph(include_library=False)
    owner = await User.create(user_id="vis_owner2", display_name="Owner")
    ws, app, _nested, _standalone = await _org_fixture(
        owner=owner, app_visibility="private"
    )
    member = await User.create(user_id="vis_member2", display_name="Member")
    await _ws_member(ws, member)

    assert await resolve_role(member.id, "app", app.id) is None
    assert await get_user_accessible_apps(member.id) == []


@pytest.mark.asyncio
async def test_member_sees_nested_inherit_track_under_workspace_visible_app():
    await ensure_integral_app_graph(include_library=False)
    owner = await User.create(user_id="vis_owner3", display_name="Owner")
    ws, app, nested, _standalone = await _org_fixture(
        owner=owner, app_visibility="workspace", nested_track_visibility="inherit"
    )
    member = await User.create(user_id="vis_member3", display_name="Member")
    await _ws_member(ws, member)

    assert await resolve_role(member.id, "track", nested.id) == "viewer"
    assert nested.id in {t.id for t in await get_user_accessible_tracks(member.id)}


@pytest.mark.asyncio
async def test_member_cannot_see_private_track_under_workspace_visible_app():
    await ensure_integral_app_graph(include_library=False)
    owner = await User.create(user_id="vis_owner4", display_name="Owner")
    ws, app, nested, _standalone = await _org_fixture(
        owner=owner,
        app_visibility="workspace",
        nested_track_visibility="private",
    )
    member = await User.create(user_id="vis_member4", display_name="Member")
    await _ws_member(ws, member)

    assert await resolve_role(member.id, "app", app.id) == "viewer"
    assert await resolve_role(member.id, "track", nested.id) is None


@pytest.mark.asyncio
async def test_guest_cannot_see_workspace_visible_app():
    await ensure_integral_app_graph(include_library=False)
    owner = await User.create(user_id="vis_owner5", display_name="Owner")
    ws, app, _nested, _standalone = await _org_fixture(
        owner=owner, app_visibility="workspace"
    )
    guest = await User.create(user_id="vis_guest1", display_name="Guest")
    await _ws_member(ws, guest, role="guest")

    assert await resolve_role(guest.id, "app", app.id) is None


@pytest.mark.asyncio
async def test_guest_with_explicit_collaborator_edge_can_see_app():
    await ensure_integral_app_graph(include_library=False)
    owner = await User.create(user_id="vis_owner6", display_name="Owner")
    ws, app, _nested, _standalone = await _org_fixture(
        owner=owner, app_visibility="workspace"
    )
    guest = await User.create(user_id="vis_guest2", display_name="Guest")
    await _ws_member(ws, guest, role="guest")
    now = datetime.now().isoformat()
    await guest.connect(
        app, edge=COLLABORATES_ON, role="viewer", invited_at=now, invited_by=owner.id
    )

    assert await resolve_role(guest.id, "app", app.id) == "viewer"
    assert app.id in {a.id for a in await get_user_accessible_apps(guest.id)}


@pytest.mark.asyncio
async def test_add_collaborator_materializes_guest_and_grants_access():
    await ensure_integral_app_graph(include_library=False)
    owner = await User.create(user_id="vis_owner7", display_name="Owner")
    ws, app, _nested, _standalone = await _org_fixture(
        owner=owner, app_visibility="private"
    )
    outsider = await User.create(user_id="vis_outsider", display_name="Outsider")
    await catalog_user(outsider)

    result = await add_collaborator(owner.id, "app", app.id, outsider.id, role="viewer")
    assert result["auto_added_to_workspace_pool"] is True

    assert await resolve_role(outsider.id, "app", app.id) == "viewer"
    assert app.id in {a.id for a in await get_user_accessible_apps(outsider.id)}


@pytest.mark.asyncio
async def test_app_editor_beats_workspace_visibility_viewer_on_track():
    """Visibility is a read floor, not a ceiling (strongest-wins).

    An App editor who is also a workspace member of a workspace-visible
    track must resolve as editor — otherwise comment/edit gates fail for
    users with full App permissions (QA: cannot add comments).
    """
    await ensure_integral_app_graph(include_library=False)
    owner = await User.create(user_id="vis_owner_strong", display_name="Owner")
    ws, app, nested, _standalone = await _org_fixture(
        owner=owner,
        app_visibility="workspace",
        nested_track_visibility="inherit",
    )
    editor = await User.create(user_id="vis_editor_strong", display_name="Editor")
    await _ws_member(ws, editor)
    await editor.connect(app, edge=COLLABORATES_ON, role="editor")

    assert await resolve_role(editor.id, "app", app.id) == "editor"
    assert await resolve_role(editor.id, "track", nested.id) == "editor"


@pytest.mark.asyncio
async def test_app_commenter_beats_workspace_visibility_viewer_on_track():
    """App commenter cascade must survive a workspace visibility grant."""
    await ensure_integral_app_graph(include_library=False)
    owner = await User.create(user_id="vis_owner_cm", display_name="Owner")
    ws, app, nested, _standalone = await _org_fixture(
        owner=owner,
        app_visibility="workspace",
        nested_track_visibility="inherit",
    )
    commenter = await User.create(user_id="vis_cm_strong", display_name="Commenter")
    await _ws_member(ws, commenter)
    await commenter.connect(app, edge=COLLABORATES_ON, role="commenter")

    assert await resolve_role(commenter.id, "track", nested.id) == "commenter"


@pytest.mark.asyncio
async def test_app_editor_beats_public_visibility_viewer_on_track():
    """Public visibility is also a floor — App editor must still resolve editor."""
    await ensure_integral_app_graph(include_library=False)
    owner = await User.create(user_id="vis_owner_pub", display_name="Owner")
    ws, app, nested, _standalone = await _org_fixture(
        owner=owner,
        app_visibility="private",
        nested_track_visibility="public",
    )
    editor = await User.create(user_id="vis_editor_pub", display_name="Editor")
    await _ws_member(ws, editor)
    await editor.connect(app, edge=COLLABORATES_ON, role="editor")

    assert await resolve_role(editor.id, "track", nested.id) == "editor"


@pytest.mark.asyncio
async def test_list_collaborators_includes_caller_role_for_staff(
    test_user,
    second_user,
    second_user_client: AsyncClient,
):
    """Org staff get caller_role even when not enumerated in collaborators."""
    await ensure_integral_app_graph(include_library=False)
    owner = await User.get(test_user.id)
    assert owner is not None
    ws, _app, nested, _standalone = await _org_fixture(
        owner=owner,
        nested_track_visibility="public",
    )
    staff = await User.get(second_user.id)
    assert staff is not None
    now = datetime.now().isoformat()
    await staff.connect(
        ws,
        edge=IS_MEMBER_OF,
        role="admin",
        joined_at=now,
        can_create_apps=True,
        can_create_tracks=True,
    )

    resp = await second_user_client.get(f"/api/tracks/{nested.id}/collaborators")
    assert resp.status_code == 200
    body = resp.json()
    # Wave 1: org staff hold inventory access, not implicit authority —
    # workspace_staff_implicit_resource_role returns "commenter" (raised from
    # "viewer" so staff can answer comments on content they administer).
    # Managing the resource still requires a direct grant.
    # See tests/test_wave1_access.py.
    assert body.get("caller_role") == "commenter"
    assert body.get("visibility_grant") == "public"
    collab_ids = {
        (c.get("id") or c.get("user_id")) for c in (body.get("collaborators") or [])
    }
    assert staff.id not in collab_ids


@pytest.mark.asyncio
async def test_excluded_from_blocks_visibility_grant_on_track():
    await ensure_integral_app_graph(include_library=False)
    owner = await User.create(user_id="vis_owner8", display_name="Owner")
    ws, _app, _nested, standalone = await _org_fixture(
        owner=owner,
        standalone_track_visibility="workspace",
    )
    member = await User.create(user_id="vis_member8", display_name="Member")
    await _ws_member(ws, member)
    await member.connect(
        standalone, edge=EXCLUDED_FROM, excluded_at=datetime.now().isoformat()
    )

    assert await resolve_role(member.id, "track", standalone.id) is None


@pytest.mark.asyncio
async def test_public_app_grants_viewer_to_any_authenticated_user():
    await ensure_integral_app_graph(include_library=False)
    owner = await User.create(user_id="vis_owner9", display_name="Owner")
    _ws, app, _nested, _standalone = await _org_fixture(
        owner=owner, app_visibility="public"
    )
    stranger = await User.create(user_id="vis_stranger", display_name="Stranger")

    assert await resolve_role(stranger.id, "app", app.id) == "viewer"


@pytest.mark.asyncio
async def test_member_list_apps_scoped_to_org_workspace(
    test_user, second_user, second_user_client: AsyncClient
):
    await ensure_integral_app_graph(include_library=False)
    owner = await User.get(test_user.id)
    assert owner is not None

    ws, app, _nested, _standalone = await _org_fixture(
        owner=owner, app_visibility="workspace"
    )
    member = await User.get(second_user.id)
    assert member is not None
    await _ws_member(ws, member)

    resp = await second_user_client.get(
        "/api/apps",
        headers={"X-Integral-Scope": f"ws:{ws.id}"},
    )
    assert resp.status_code == 200
    ids = [a["id"] for a in resp.json()["apps"]]
    assert app.id in ids
