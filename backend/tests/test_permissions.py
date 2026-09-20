"""Tests for the four-level permission service."""

import pytest

from app.models.edges import COLLABORATES_ON, CONTAINS, IS_MEMBER_OF, OWNS
from app.models.nodes import App, Entry, Track, User, View, Workspace
from app.services.permissions import (
    can_delete_track,
    can_edit_app,
    can_edit_entry,
    can_edit_track,
    can_edit_view,
    can_view_app,
    can_view_entry,
    can_view_track,
    can_view_view,
    get_user_accessible_entries,
    resolve_role,
)

# Part of the per-PR smoke gate (see pyproject [tool.pytest.ini_options] markers).
# CI bills only this subset; the full suite runs locally via `make verify` and nightly.
pytestmark = pytest.mark.smoke

# ---------------------------------------------------------------------------
# Track-level permissions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_can_view_track():
    user = await User.create(user_id="perm_owner_1", display_name="Owner")
    track = await Track.create(title="My Track", owner_id=user.id)
    await user.connect(track, edge=OWNS)

    assert await can_view_track(user.id, track.id) is True


@pytest.mark.asyncio
async def test_collaborator_viewer_can_view_track():
    owner = await User.create(user_id="perm_owner_2", display_name="Owner")
    viewer = await User.create(user_id="perm_viewer_1", display_name="Viewer")
    track = await Track.create(title="Shared Track", owner_id=owner.id)
    await owner.connect(track, edge=OWNS)
    await viewer.connect(track, edge=COLLABORATES_ON, role="viewer")

    assert await can_view_track(viewer.id, track.id) is True


@pytest.mark.asyncio
async def test_collaborator_viewer_cannot_edit_track():
    owner = await User.create(user_id="perm_owner_3", display_name="Owner")
    viewer = await User.create(user_id="perm_viewer_2", display_name="Viewer")
    track = await Track.create(title="Read-Only Track", owner_id=owner.id)
    await owner.connect(track, edge=OWNS)
    await viewer.connect(track, edge=COLLABORATES_ON, role="viewer")

    assert await can_edit_track(viewer.id, track.id) is False


@pytest.mark.asyncio
async def test_editor_can_edit_track():
    owner = await User.create(user_id="perm_owner_4", display_name="Owner")
    editor = await User.create(user_id="perm_editor_1", display_name="Editor")
    track = await Track.create(title="Editable Track", owner_id=owner.id)
    await owner.connect(track, edge=OWNS)
    await editor.connect(track, edge=COLLABORATES_ON, role="editor")

    assert await can_edit_track(editor.id, track.id) is True


@pytest.mark.asyncio
async def test_only_owner_can_delete_track():
    owner = await User.create(user_id="perm_owner_5", display_name="Owner")
    editor = await User.create(user_id="perm_editor_2", display_name="Editor")
    track = await Track.create(title="Delete Track", owner_id=owner.id)
    await owner.connect(track, edge=OWNS)
    await editor.connect(track, edge=COLLABORATES_ON, role="editor")

    assert await can_delete_track(owner.id, track.id) is True
    assert await can_delete_track(editor.id, track.id) is False


@pytest.mark.asyncio
async def test_public_standalone_track_grants_viewer():
    """``visibility="public"`` on a standalone track grants read to any
    authenticated user (ARCHITECTURE §9.5 step 4)."""
    user = await User.create(user_id="perm_anon_1", display_name="Anon")
    track = await Track.create(
        title="Public Track",
        owner_id="someone_else",
        visibility="public",
    )

    assert await can_view_track(user.id, track.id) is True
    assert await resolve_role(user.id, "track", track.id) == "viewer"


@pytest.mark.asyncio
async def test_track_owner_still_edits_under_new_visibility_semantics():
    """``OWNS`` always grants owner regardless of visibility value."""
    owner = await User.create(user_id="perm_pub_owner", display_name="Pub Owner")
    track = await Track.create(
        title="My Track", owner_id=owner.id, visibility="inherit"
    )
    await owner.connect(track, edge=OWNS)

    assert await can_edit_track(owner.id, track.id) is True
    assert await can_delete_track(owner.id, track.id) is True


@pytest.mark.asyncio
async def test_private_track_not_visible_to_stranger():
    user = await User.create(user_id="perm_stranger_1", display_name="Stranger")
    track = await Track.create(
        title="Private Track", owner_id="owner_xyz", visibility="private"
    )

    assert await can_view_track(user.id, track.id) is False


# ---------------------------------------------------------------------------
# App-level permissions with cascade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_space_owner_can_view():
    user = await User.create(user_id="ts_perm_1", display_name="TS Owner")
    ts = await App.create(name="My App", owner_user_id=user.id)
    await user.connect(ts, edge=OWNS)

    assert await can_view_app(user.id, ts.id) is True


@pytest.mark.asyncio
async def test_space_editor_can_edit():
    owner = await User.create(user_id="ts_perm_2", display_name="Owner")
    editor = await User.create(user_id="ts_perm_3", display_name="Editor")
    ts = await App.create(name="Editable App", owner_user_id=owner.id)
    await owner.connect(ts, edge=OWNS)
    await editor.connect(ts, edge=COLLABORATES_ON, role="editor")

    assert await can_edit_app(editor.id, ts.id) is True


@pytest.mark.asyncio
async def test_space_viewer_cannot_edit():
    owner = await User.create(user_id="ts_perm_4", display_name="Owner")
    viewer = await User.create(user_id="ts_perm_5", display_name="Viewer")
    ts = await App.create(name="Read App", owner_user_id=owner.id)
    await owner.connect(ts, edge=OWNS)
    await viewer.connect(ts, edge=COLLABORATES_ON, role="viewer")

    assert await can_edit_app(viewer.id, ts.id) is False


@pytest.mark.asyncio
async def test_space_cascade_grants_track_access():
    """App editor should inherit track access via cascade."""
    owner = await User.create(user_id="ts_cas_1", display_name="Owner")
    collab = await User.create(user_id="ts_cas_2", display_name="Collab")
    ts = await App.create(name="Cascade App", owner_user_id=owner.id)
    track = await Track.create(title="Cascaded Track", owner_id=owner.id)
    await owner.connect(ts, edge=OWNS)
    await ts.connect(track, edge=CONTAINS)
    await collab.connect(ts, edge=COLLABORATES_ON, role="editor")

    assert await can_view_track(collab.id, track.id) is True
    assert await can_edit_track(collab.id, track.id) is True


@pytest.mark.asyncio
async def test_space_cascade_viewer_cannot_edit_track():
    owner = await User.create(user_id="ts_cas_3", display_name="Owner")
    viewer = await User.create(user_id="ts_cas_4", display_name="Viewer")
    ts = await App.create(name="Viewer App", owner_user_id=owner.id)
    track = await Track.create(title="Cascaded Track 2", owner_id=owner.id)
    await owner.connect(ts, edge=OWNS)
    await ts.connect(track, edge=CONTAINS)
    await viewer.connect(ts, edge=COLLABORATES_ON, role="viewer")

    assert await can_view_track(viewer.id, track.id) is True
    assert await can_edit_track(viewer.id, track.id) is False


# ---------------------------------------------------------------------------
# Entry-level (track access only; no per-entry visibility)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entry_visible_all_track_members():
    """Any track member who can view the track can view entries in it."""
    owner = await User.create(user_id="ev_1", display_name="Owner")
    viewer = await User.create(user_id="ev_2", display_name="Viewer")
    track = await Track.create(title="VR Track", owner_id=owner.id)
    entry = await Entry.create(
        title="Open Entry", author_id=owner.id, track_id=track.id
    )
    await owner.connect(track, edge=OWNS)
    await viewer.connect(track, edge=COLLABORATES_ON, role="viewer")
    await track.connect(entry, edge=CONTAINS)

    assert await can_view_entry(viewer.id, entry.id) is True


@pytest.mark.asyncio
async def test_entry_visible_to_all_track_viewers_no_per_entry_acl():
    """Viewers see all entries in a track when they can view the track."""
    owner = await User.create(user_id="ev_3", display_name="Owner")
    other = await User.create(user_id="ev_4", display_name="Other")
    track = await Track.create(title="Shared Track", owner_id=owner.id)
    entry = await Entry.create(
        title="Entry",
        author_id=owner.id,
        track_id=track.id,
    )
    await owner.connect(track, edge=OWNS)
    await other.connect(track, edge=COLLABORATES_ON, role="viewer")
    await track.connect(entry, edge=CONTAINS)

    assert await can_view_entry(owner.id, entry.id) is True
    assert await can_view_entry(other.id, entry.id) is True


@pytest.mark.asyncio
async def test_author_needs_track_access_to_view_entry():
    """Author without collaboration on the track cannot view the entry."""
    author = await User.create(user_id="ev_7_author", display_name="Author")
    owner = await User.create(user_id="ev_7_owner", display_name="TrackOwner")
    track = await Track.create(title="Author Track", owner_id=owner.id)
    await owner.connect(track, edge=OWNS)
    entry = await Entry.create(title="My Entry", author_id=author.id, track_id=track.id)
    await track.connect(entry, edge=CONTAINS)

    assert await can_view_entry(author.id, entry.id) is False

    await author.connect(track, edge=COLLABORATES_ON, role="editor")
    assert await can_view_entry(author.id, entry.id) is True


# ---------------------------------------------------------------------------
# View-level permissions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_view_inherits_track_access():
    """View access defaults to track access."""
    owner = await User.create(user_id="vp_1", display_name="Owner")
    viewer = await User.create(user_id="vp_2", display_name="Viewer")
    track = await Track.create(title="View Track", owner_id=owner.id)
    view = await View.create(
        name="Board", type="kanban", track_id=track.id, created_by=owner.id
    )
    await owner.connect(track, edge=OWNS)
    await viewer.connect(track, edge=COLLABORATES_ON, role="viewer")

    assert await can_view_view(viewer.id, view.id) is True
    assert await can_edit_view(viewer.id, view.id) is False


@pytest.mark.asyncio
async def test_get_user_accessible_entries_batched_multiple_tracks():
    """Unscoped listing uses one batched track_id query (behavior: all visible entries)."""
    owner = await User.create(user_id="batch_e1", display_name="O")
    t1 = await Track.create(title="T1", owner_id=owner.id)
    t2 = await Track.create(title="T2", owner_id=owner.id)
    await owner.connect(t1, edge=OWNS)
    await owner.connect(t2, edge=OWNS)
    e1 = await Entry.create(title="A", author_id=owner.id, track_id=t1.id)
    e2 = await Entry.create(title="B", author_id=owner.id, track_id=t2.id)
    await t1.connect(e1, edge=CONTAINS)
    await t2.connect(e2, edge=CONTAINS)

    got = await get_user_accessible_entries(owner.id)
    got_ids = {e.id for e in got}
    assert got_ids == {e1.id, e2.id}


@pytest.mark.asyncio
async def test_space_scoped_entries_do_not_include_author_entries_outside_space():
    """App feed matches per-track listing: no global author merge for that app_node."""
    owner = await User.create(user_id="sp_scope_1", display_name="O")
    sp = await App.create(name="S", owner_user_id=owner.id)
    t_in = await Track.create(title="In", owner_id=owner.id)
    t_out = await Track.create(title="Out", owner_id=owner.id)
    await owner.connect(sp, edge=OWNS)
    await owner.connect(t_in, edge=OWNS)
    await owner.connect(t_out, edge=OWNS)
    await sp.connect(t_in, edge=CONTAINS)
    e_in = await Entry.create(title="In entry", author_id=owner.id, track_id=t_in.id)
    e_out = await Entry.create(title="Out entry", author_id=owner.id, track_id=t_out.id)
    await t_in.connect(e_in, edge=CONTAINS)
    await t_out.connect(e_out, edge=CONTAINS)

    got = await get_user_accessible_entries(owner.id, app_id=sp.id)
    got_ids = {e.id for e in got}
    assert e_in.id in got_ids
    assert e_out.id not in got_ids


@pytest.mark.asyncio
async def test_view_admin_can_edit_editor_cannot():
    """Track-config split: views are admin-only substrate.

    Editor role grants entry CRUD authority but NOT view-config mutation;
    admin role does. Owner inherits view-edit via OWNS. Mirrors the
    permissions split introduced when track-config was separated from
    entry-CRUD in the COLLABORATES_ON role ladder.
    """
    owner = await User.create(user_id="vp_3", display_name="Owner")
    admin = await User.create(user_id="vp_admin", display_name="Admin")
    editor = await User.create(user_id="vp_4", display_name="Editor")
    track = await Track.create(title="Edit View Track", owner_id=owner.id)
    view = await View.create(
        name="Table", type="table", track_id=track.id, created_by=owner.id
    )
    await owner.connect(track, edge=OWNS)
    await admin.connect(track, edge=COLLABORATES_ON, role="admin")
    await editor.connect(track, edge=COLLABORATES_ON, role="editor")

    assert await can_edit_view(owner.id, view.id) is True
    assert await can_edit_view(admin.id, view.id) is True
    assert await can_edit_view(editor.id, view.id) is False


@pytest.mark.asyncio
async def test_private_track_cascades_from_space_editor_with_workspace_membership():
    """Regression: ``visibility="private"`` must NOT block App cascade.

    Mirrors the dev-monorepo-rebuild probe: an organization-kind workspace
    owner adds a member, owns a private App + private Track in that
    workspace, and grants the member editor role on the App. Per
    AGENTS.md rule 3 and ARCHITECTURE §9.5, the member must resolve to
    editor on every track in that space — only ``EXCLUDED_FROM`` overrides
    cascade.
    """
    owner = await User.create(user_id="cv_priv_a", display_name="Owner A")
    member = await User.create(user_id="cv_priv_b", display_name="Member B")
    ws = await Workspace.create(name="ProbeCo Cascade", kind="organization")
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner")
    await member.connect(
        ws,
        edge=IS_MEMBER_OF,
        role="member",
        can_create_apps=True,
        can_create_tracks=True,
    )

    sp = await App.create(
        name="Cascade Probe App",
        owner_user_id=owner.id,
        workspace_id=ws.id,
        visibility="private",
    )
    await owner.connect(sp, edge=OWNS)

    track = await Track.create(
        title="Cascade Probe Track",
        owner_id=owner.id,
        workspace_id=ws.id,
        visibility="private",
    )
    await owner.connect(track, edge=OWNS)
    await sp.connect(track, edge=CONTAINS)

    await member.connect(sp, edge=COLLABORATES_ON, role="editor")

    assert await can_view_track(member.id, track.id) is True
    assert await can_edit_track(member.id, track.id) is True
