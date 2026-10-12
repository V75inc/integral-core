"""Tests for the four-level permission service."""

import pytest

from app.exceptions import AuthorizationUnavailableError
from app.models.edges import (
    COLLABORATES_ON,
    CONTAINS,
    EXCLUDED_FROM,
    IS_MEMBER_OF,
    OWNS,
)
from app.models.nodes import Entry
from app.services.app_graph import create_track_view
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
    count_user_accessible_entries,
    get_user_accessible_entries,
    get_user_accessible_entry_page,
    iter_user_accessible_entry_batches,
    resolve_role,
)
from tests.rooted_access_fixture import rooted_access_graph

# Part of the per-PR smoke gate (see pyproject [tool.pytest.ini_options] markers).
# CI bills only this subset; the full suite runs locally via `make verify` and nightly.
pytestmark = pytest.mark.smoke

# ---------------------------------------------------------------------------
# Track-level permissions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_can_view_track(rooted_access_graph):
    user = await rooted_access_graph.user(user_id="perm_owner_1", display_name="Owner")
    track = await rooted_access_graph.track(title="My Track", owner_id=user.id)
    await user.connect(track, edge=OWNS)

    assert await can_view_track(user.id, track.id) is True


@pytest.mark.asyncio
async def test_collaborator_viewer_can_view_track(rooted_access_graph):
    owner = await rooted_access_graph.user(user_id="perm_owner_2", display_name="Owner")
    viewer = await rooted_access_graph.user(
        user_id="perm_viewer_1", display_name="Viewer"
    )
    track = await rooted_access_graph.track(title="Shared Track", owner_id=owner.id)
    await owner.connect(track, edge=OWNS)
    await viewer.connect(track, edge=COLLABORATES_ON, role="viewer")

    assert await can_view_track(viewer.id, track.id) is True


@pytest.mark.asyncio
async def test_collaborator_viewer_cannot_edit_track(rooted_access_graph):
    owner = await rooted_access_graph.user(user_id="perm_owner_3", display_name="Owner")
    viewer = await rooted_access_graph.user(
        user_id="perm_viewer_2", display_name="Viewer"
    )
    track = await rooted_access_graph.track(title="Read-Only Track", owner_id=owner.id)
    await owner.connect(track, edge=OWNS)
    await viewer.connect(track, edge=COLLABORATES_ON, role="viewer")

    assert await can_edit_track(viewer.id, track.id) is False


@pytest.mark.asyncio
async def test_editor_can_edit_track(rooted_access_graph):
    owner = await rooted_access_graph.user(user_id="perm_owner_4", display_name="Owner")
    editor = await rooted_access_graph.user(
        user_id="perm_editor_1", display_name="Editor"
    )
    track = await rooted_access_graph.track(title="Editable Track", owner_id=owner.id)
    await owner.connect(track, edge=OWNS)
    await editor.connect(track, edge=COLLABORATES_ON, role="editor")

    assert await can_edit_track(editor.id, track.id) is True


@pytest.mark.asyncio
async def test_only_owner_can_delete_track(rooted_access_graph):
    owner = await rooted_access_graph.user(user_id="perm_owner_5", display_name="Owner")
    editor = await rooted_access_graph.user(
        user_id="perm_editor_2", display_name="Editor"
    )
    track = await rooted_access_graph.track(title="Delete Track", owner_id=owner.id)
    await owner.connect(track, edge=OWNS)
    await editor.connect(track, edge=COLLABORATES_ON, role="editor")

    assert await can_delete_track(owner.id, track.id) is True
    assert await can_delete_track(editor.id, track.id) is False


@pytest.mark.asyncio
async def test_public_standalone_track_grants_viewer(rooted_access_graph):
    """``visibility="public"`` on a standalone track grants read to any
    authenticated user (ARCHITECTURE §9.5 step 4)."""
    user = await rooted_access_graph.user(user_id="perm_anon_1", display_name="Anon")
    track = await rooted_access_graph.track(
        title="Public Track",
        owner_id="someone_else",
        visibility="public",
    )

    assert await can_view_track(user.id, track.id) is True
    assert await resolve_role(user.id, "track", track.id) == "viewer"


@pytest.mark.asyncio
async def test_track_owner_still_edits_under_new_visibility_semantics(
    rooted_access_graph,
):
    """``OWNS`` always grants owner regardless of visibility value."""
    owner = await rooted_access_graph.user(
        user_id="perm_pub_owner", display_name="Pub Owner"
    )
    track = await rooted_access_graph.track(
        title="My Track", owner_id=owner.id, visibility="inherit"
    )
    await owner.connect(track, edge=OWNS)

    assert await can_edit_track(owner.id, track.id) is True
    assert await can_delete_track(owner.id, track.id) is True


@pytest.mark.asyncio
async def test_private_track_not_visible_to_stranger(rooted_access_graph):
    user = await rooted_access_graph.user(
        user_id="perm_stranger_1", display_name="Stranger"
    )
    track = await rooted_access_graph.track(
        title="Private Track", owner_id="owner_xyz", visibility="private"
    )

    assert await can_view_track(user.id, track.id) is False


# ---------------------------------------------------------------------------
# App-level permissions with cascade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_space_owner_can_view(rooted_access_graph):
    user = await rooted_access_graph.user(user_id="ts_perm_1", display_name="TS Owner")
    ts = await rooted_access_graph.app(name="My App", owner_user_id=user.id)
    await user.connect(ts, edge=OWNS)

    assert await can_view_app(user.id, ts.id) is True


@pytest.mark.asyncio
async def test_space_editor_can_edit(rooted_access_graph):
    owner = await rooted_access_graph.user(user_id="ts_perm_2", display_name="Owner")
    editor = await rooted_access_graph.user(user_id="ts_perm_3", display_name="Editor")
    ts = await rooted_access_graph.app(name="Editable App", owner_user_id=owner.id)
    await owner.connect(ts, edge=OWNS)
    await editor.connect(ts, edge=COLLABORATES_ON, role="editor")

    assert await can_edit_app(editor.id, ts.id) is True


@pytest.mark.asyncio
async def test_space_viewer_cannot_edit(rooted_access_graph):
    owner = await rooted_access_graph.user(user_id="ts_perm_4", display_name="Owner")
    viewer = await rooted_access_graph.user(user_id="ts_perm_5", display_name="Viewer")
    ts = await rooted_access_graph.app(name="Read App", owner_user_id=owner.id)
    await owner.connect(ts, edge=OWNS)
    await viewer.connect(ts, edge=COLLABORATES_ON, role="viewer")

    assert await can_edit_app(viewer.id, ts.id) is False


@pytest.mark.asyncio
async def test_space_cascade_grants_track_access(rooted_access_graph):
    """App editor should inherit track access via cascade."""
    owner = await rooted_access_graph.user(user_id="ts_cas_1", display_name="Owner")
    collab = await rooted_access_graph.user(user_id="ts_cas_2", display_name="Collab")
    ts = await rooted_access_graph.app(name="Cascade App", owner_user_id=owner.id)
    track = await rooted_access_graph.track(title="Cascaded Track", owner_id=owner.id)
    await owner.connect(ts, edge=OWNS)
    await ts.connect(track, edge=CONTAINS)
    await collab.connect(ts, edge=COLLABORATES_ON, role="editor")

    assert await can_view_track(collab.id, track.id) is True
    assert await can_edit_track(collab.id, track.id) is True


@pytest.mark.asyncio
async def test_space_cascade_viewer_cannot_edit_track(rooted_access_graph):
    owner = await rooted_access_graph.user(user_id="ts_cas_3", display_name="Owner")
    viewer = await rooted_access_graph.user(user_id="ts_cas_4", display_name="Viewer")
    ts = await rooted_access_graph.app(name="Viewer App", owner_user_id=owner.id)
    track = await rooted_access_graph.track(title="Cascaded Track 2", owner_id=owner.id)
    await owner.connect(ts, edge=OWNS)
    await ts.connect(track, edge=CONTAINS)
    await viewer.connect(ts, edge=COLLABORATES_ON, role="viewer")

    assert await can_view_track(viewer.id, track.id) is True
    assert await can_edit_track(viewer.id, track.id) is False


# ---------------------------------------------------------------------------
# Entry-level (track access only; no per-entry visibility)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entry_visible_all_track_members(rooted_access_graph):
    """Any track member who can view the track can view entries in it."""
    owner = await rooted_access_graph.user(user_id="ev_1", display_name="Owner")
    viewer = await rooted_access_graph.user(user_id="ev_2", display_name="Viewer")
    track = await rooted_access_graph.track(title="VR Track", owner_id=owner.id)
    entry = await rooted_access_graph.entry(
        title="Open Entry", author_id=owner.id, track_id=track.id
    )
    await owner.connect(track, edge=OWNS)
    await viewer.connect(track, edge=COLLABORATES_ON, role="viewer")
    await track.connect(entry, edge=CONTAINS)

    assert await can_view_entry(viewer.id, entry.id) is True


@pytest.mark.asyncio
async def test_entry_visible_to_all_track_viewers_no_per_entry_acl(rooted_access_graph):
    """Viewers see all entries in a track when they can view the track."""
    owner = await rooted_access_graph.user(user_id="ev_3", display_name="Owner")
    other = await rooted_access_graph.user(user_id="ev_4", display_name="Other")
    track = await rooted_access_graph.track(title="Shared Track", owner_id=owner.id)
    entry = await rooted_access_graph.entry(
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
async def test_author_needs_track_access_to_view_entry(rooted_access_graph):
    """Author without collaboration on the track cannot view the entry."""
    author = await rooted_access_graph.user(
        user_id="ev_7_author", display_name="Author"
    )
    owner = await rooted_access_graph.user(
        user_id="ev_7_owner", display_name="TrackOwner"
    )
    track = await rooted_access_graph.track(title="Author Track", owner_id=owner.id)
    await owner.connect(track, edge=OWNS)
    entry = await rooted_access_graph.entry(
        title="My Entry", author_id=author.id, track_id=track.id
    )
    await track.connect(entry, edge=CONTAINS)

    assert await can_view_entry(author.id, entry.id) is False

    await author.connect(track, edge=COLLABORATES_ON, role="editor")
    assert await can_view_entry(author.id, entry.id) is True


# ---------------------------------------------------------------------------
# View-level permissions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_view_inherits_track_access(rooted_access_graph):
    """View access defaults to track access."""
    owner = await rooted_access_graph.user(user_id="vp_1", display_name="Owner")
    viewer = await rooted_access_graph.user(user_id="vp_2", display_name="Viewer")
    track = await rooted_access_graph.track(title="View Track", owner_id=owner.id)
    view = await create_track_view(
        track, name="Board", type="kanban", track_id=track.id, created_by=owner.id
    )
    await owner.connect(track, edge=OWNS)
    await viewer.connect(track, edge=COLLABORATES_ON, role="viewer")

    assert await can_view_view(viewer.id, view.id) is True
    assert await can_edit_view(viewer.id, view.id) is False


@pytest.mark.asyncio
async def test_get_user_accessible_entries_batched_multiple_tracks(rooted_access_graph):
    """Unscoped listing uses one batched track_id query (behavior: all visible entries)."""
    owner = await rooted_access_graph.user(user_id="batch_e1", display_name="O")
    t1 = await rooted_access_graph.track(title="T1", owner_id=owner.id)
    t2 = await rooted_access_graph.track(title="T2", owner_id=owner.id)
    await owner.connect(t1, edge=OWNS)
    await owner.connect(t2, edge=OWNS)
    e1 = await rooted_access_graph.entry(title="A", author_id=owner.id, track_id=t1.id)
    e2 = await rooted_access_graph.entry(title="B", author_id=owner.id, track_id=t2.id)
    await t1.connect(e1, edge=CONTAINS)
    await t2.connect(e2, edge=CONTAINS)

    got = await get_user_accessible_entries(owner.id)
    got_ids = {e.id for e in got}
    assert got_ids == {e1.id, e2.id}

    pushed = await get_user_accessible_entries(
        owner.id, candidate_query={"context.title": "A"}, strict=True
    )
    assert {entry.id for entry in pushed} == {e1.id}


@pytest.mark.asyncio
async def test_get_user_accessible_entries_batches_entry_overrides(rooted_access_graph):
    """Entry exclusions block Track inheritance; direct Entry grants win."""
    owner = await rooted_access_graph.user(user_id="batch_entry_acl", display_name="O")
    track = await rooted_access_graph.track(title="Entry ACL Track", owner_id=owner.id)
    await owner.connect(track, edge=OWNS)
    excluded = await rooted_access_graph.entry(
        title="Excluded", author_id=owner.id, track_id=track.id
    )
    direct = await rooted_access_graph.entry(
        title="Direct", author_id=owner.id, track_id=track.id
    )
    inherited = await rooted_access_graph.entry(
        title="Inherited", author_id=owner.id, track_id=track.id
    )
    for entry in (excluded, direct, inherited):
        await track.connect(entry, edge=CONTAINS)

    await owner.connect(excluded, edge=EXCLUDED_FROM)
    await owner.connect(direct, edge=EXCLUDED_FROM)
    await owner.connect(direct, edge=COLLABORATES_ON, role="viewer")

    got = await get_user_accessible_entries(owner.id, track_id=track.id, strict=True)
    assert {entry.id for entry in got} == {direct.id, inherited.id}


@pytest.mark.asyncio
async def test_paged_entry_reads_preserve_direct_grants_and_exclusions(
    rooted_access_graph,
):
    owner = await rooted_access_graph.user(user_id="paged_entry_acl", display_name="O")
    track = await rooted_access_graph.track(title="Paged ACL Track", owner_id=owner.id)
    await owner.connect(track, edge=OWNS)
    excluded = await rooted_access_graph.entry(
        title="A excluded", author_id=owner.id, track_id=track.id
    )
    direct = await rooted_access_graph.entry(
        title="B direct", author_id=owner.id, track_id=track.id
    )
    inherited = await rooted_access_graph.entry(
        title="C inherited", author_id=owner.id, track_id=track.id
    )
    for entry in (excluded, direct, inherited):
        await track.connect(entry, edge=CONTAINS)
    await owner.connect(excluded, edge=EXCLUDED_FROM)
    await owner.connect(direct, edge=EXCLUDED_FROM)
    await owner.connect(direct, edge=COLLABORATES_ON, role="viewer")

    page = await get_user_accessible_entry_page(
        owner.id,
        track.id,
        limit=1,
        sort=[("context.title", 1)],
    )
    assert page["total"] == 2
    assert [entry.id for entry in page["entries"]] == [direct.id]
    assert await count_user_accessible_entries(owner.id, track.id) == 2

    streamed = []
    async for batch in iter_user_accessible_entry_batches(
        owner.id, track.id, batch_size=1
    ):
        streamed.extend(entry.id for entry in batch)
    assert set(streamed) == {direct.id, inherited.id}


@pytest.mark.asyncio
async def test_space_scoped_entries_do_not_include_author_entries_outside_space(
    rooted_access_graph,
):
    """App feed matches per-track listing: no global author merge for that app_node."""
    owner = await rooted_access_graph.user(user_id="sp_scope_1", display_name="O")
    sp = await rooted_access_graph.app(name="S", owner_user_id=owner.id)
    t_in = await rooted_access_graph.track(title="In", owner_id=owner.id)
    t_out = await rooted_access_graph.track(title="Out", owner_id=owner.id)
    await owner.connect(sp, edge=OWNS)
    await owner.connect(t_in, edge=OWNS)
    await owner.connect(t_out, edge=OWNS)
    await sp.connect(t_in, edge=CONTAINS)
    e_in = await rooted_access_graph.entry(
        title="In entry", author_id=owner.id, track_id=t_in.id
    )
    e_out = await rooted_access_graph.entry(
        title="Out entry", author_id=owner.id, track_id=t_out.id
    )
    await t_in.connect(e_in, edge=CONTAINS)
    await t_out.connect(e_out, edge=CONTAINS)

    got = await get_user_accessible_entries(owner.id, app_id=sp.id)
    got_ids = {e.id for e in got}
    assert e_in.id in got_ids
    assert e_out.id not in got_ids


@pytest.mark.asyncio
async def test_view_admin_can_edit_editor_cannot(rooted_access_graph):
    """Track-config split: views are admin-only substrate.

    Editor role grants entry CRUD authority but NOT view-config mutation;
    admin role does. Owner inherits view-edit via OWNS. Mirrors the
    permissions split introduced when track-config was separated from
    entry-CRUD in the COLLABORATES_ON role ladder.
    """
    owner = await rooted_access_graph.user(user_id="vp_3", display_name="Owner")
    admin = await rooted_access_graph.user(user_id="vp_admin", display_name="Admin")
    editor = await rooted_access_graph.user(user_id="vp_4", display_name="Editor")
    track = await rooted_access_graph.track(title="Edit View Track", owner_id=owner.id)
    view = await create_track_view(
        track, name="Table", type="table", track_id=track.id, created_by=owner.id
    )
    await owner.connect(track, edge=OWNS)
    await admin.connect(track, edge=COLLABORATES_ON, role="admin")
    await editor.connect(track, edge=COLLABORATES_ON, role="editor")

    assert await can_edit_view(owner.id, view.id) is True
    assert await can_edit_view(admin.id, view.id) is True
    assert await can_edit_view(editor.id, view.id) is False


@pytest.mark.asyncio
async def test_private_track_cascades_from_space_editor_with_workspace_membership(
    rooted_access_graph,
):
    """Regression: ``visibility="private"`` must NOT block App cascade.

    Mirrors the dev-monorepo-rebuild probe: an organization-kind workspace
    owner adds a member, owns a private App + private Track in that
    workspace, and grants the member editor role on the App. Per
    AGENTS.md rule 3 and ARCHITECTURE §9.5, the member must resolve to
    editor on every track in that space — only ``EXCLUDED_FROM`` overrides
    cascade.
    """
    owner = await rooted_access_graph.user(user_id="cv_priv_a", display_name="Owner A")
    member = await rooted_access_graph.user(
        user_id="cv_priv_b", display_name="Member B"
    )
    ws = await rooted_access_graph.workspace(
        name="ProbeCo Cascade", kind="organization"
    )
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner")
    await member.connect(
        ws,
        edge=IS_MEMBER_OF,
        role="member",
        can_create_apps=True,
        can_create_tracks=True,
    )

    sp = await rooted_access_graph.app(
        name="Cascade Probe App",
        owner_user_id=owner.id,
        workspace_id=ws.id,
        visibility="private",
    )
    await owner.connect(sp, edge=OWNS)

    track = await rooted_access_graph.track(
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


@pytest.mark.asyncio
@pytest.mark.parametrize("track_scoped", [True, False])
async def test_feed_listing_excludes_entries_before_pagination_and_counts(
    track_scoped, rooted_access_graph
):
    from app.services.entry_listing import fetch_accessible_entries_page

    user = await rooted_access_graph.user(
        user_id="feed_entry_acl", display_name="Reader"
    )
    track = await rooted_access_graph.track(title="Feed ACL Track", owner_id=user.id)
    await user.connect(track, edge=OWNS)
    hidden = await Entry.create(title="Hidden", track_id=track.id, author_id=user.id)
    direct = await Entry.create(title="Direct", track_id=track.id, author_id=user.id)
    inherited = await Entry.create(
        title="Inherited", track_id=track.id, author_id=user.id
    )
    for entry in (hidden, direct, inherited):
        entry.created_at = entry.updated_at = "2026-10-11T12:00:00+00:00"
        await entry.save()
        await track.connect(entry, edge=CONTAINS)
    await user.connect(hidden, edge=EXCLUDED_FROM)
    await user.connect(direct, edge=EXCLUDED_FROM)
    await user.connect(direct, edge=COLLABORATES_ON, role="viewer")

    seen = []
    cursor = None
    for _ in range(3):
        page, response = await fetch_accessible_entries_page(
            user.id,
            track_id=track.id if track_scoped else None,
            cursor=cursor,
            limit=1,
            include_total=True,
        )
        assert response["total"] == 2
        assert len(page) == 1
        seen.extend(entry.id for entry in page)
        cursor = response["next_cursor"]
        if not response["has_more"]:
            break
    assert set(seen) == {direct.id, inherited.id}
    assert len(seen) == 2

    # Corrupt authority must fail closed rather than masquerade as an empty
    # page; preserve the historical invalid-role boundary independently.
    await user.connect(hidden, edge=COLLABORATES_ON, role="invalid_legacy_role")
    with pytest.raises(AuthorizationUnavailableError):
        await fetch_accessible_entries_page(
            user.id,
            track_id=track.id if track_scoped else None,
            limit=1,
            include_total=True,
        )


@pytest.mark.asyncio
async def test_feed_listing_resolves_authenticated_principal_to_graph_user(
    authenticated_client, test_user, test_user2
):
    from tests.fixtures.workspaces import make_org_workspace

    # An ordinary member/Track owner, not the protected Workspace owner.
    workspace = await make_org_workspace("Feed aliases", owner=test_user2)
    await test_user.connect(
        workspace,
        edge=IS_MEMBER_OF,
        role="member",
        can_create_apps=True,
        can_create_tracks=True,
    )
    headers = {"X-Integral-Scope": f"ws:{workspace.id}"}
    assert test_user.id != test_user.user_id
    response = await authenticated_client.post(
        "/api/tracks", json={"title": "Authenticated feed privacy"}, headers=headers
    )
    assert response.status_code == 200
    track_id = response.json()["track"]["id"]
    response = await authenticated_client.post(
        "/api/entries",
        json={"track_id": track_id, "title": "Private feed record"},
        headers=headers,
    )
    assert response.status_code == 200
    entry = await Entry.get(response.json()["entry"]["id"])
    await test_user.connect(entry, edge=EXCLUDED_FROM)
    response = await authenticated_client.get(
        "/api/entries",
        params={"track_id": track_id, "include_total": True},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["entries"] == []
    assert response.json()["total"] == 0
