"""Matrix tests for the unified ``resolve_role`` walker.

Phase 1d unified resolver. Covers:
  * Direct OWNS and COLLABORATES_ON grants on every resource type.
  * App cascade onto Track (and onto Entry, transitively).
  * Per-user EXCLUDED_FROM on App / Track / Entry as the sole cascade
    override (AGENTS.md rule 3 / ARCHITECTURE §9.5).
  * Workspace membership does NOT cascade to children (rule 2).
  * Personal-workspace owner resolved via IS_MEMBER_OF{role:"owner"}.
  * Role demotion: inherited "owner" caps to "editor" on children.
  * Commenter role end-to-end.
  * Track ``visibility="private"`` blocks workspace-wide visibility
    inheritance but not App collaborator cascade (see
    ``test_visibility_access_cascade.py``).

These tests exercise the resolver directly via graph fixtures — no HTTP.
"""

import pytest

from app.models.edges import (
    COLLABORATES_ON,
    CONTAINS,
    EXCLUDED_FROM,
    IS_MEMBER_OF,
    OWNS,
)
from app.models.nodes import App, Entry, Track, User, Workspace
from app.services.permissions import resolve_role

# Part of the per-PR smoke gate (see pyproject [tool.pytest.ini_options] markers).
# CI bills only this subset; the full suite runs locally via `make verify` and nightly.
pytestmark = pytest.mark.smoke


async def _mk_user(suffix: str) -> User:
    return await User.create(user_id=f"rr_user_{suffix}", display_name=f"User {suffix}")


async def _mk_workspace(name: str, kind: str = "organization") -> Workspace:
    return await Workspace.create(kind=kind, name=name, name_fold=name.casefold())


async def _mk_space(
    name: str, workspace_id: str = "", visibility: str = "inherit"
) -> App:
    sp = await App.create(
        name=name,
        name_fold=name.casefold(),
        workspace_id=workspace_id,
        visibility=visibility,
    )
    return sp


async def _mk_track(
    title: str, workspace_id: str = "", visibility: str = "inherit"
) -> Track:
    return await Track.create(
        title=title,
        title_fold=title.casefold(),
        workspace_id=workspace_id,
        visibility=visibility,
    )


async def _mk_entry(title: str, track_id: str, visibility: str = "inherit") -> Entry:
    return await Entry.create(
        title=title,
        track_id=track_id,
        visibility=visibility,
        author_id="rr_author",
    )


# ---------------------------------------------------------------------------
# Track-level direct grants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owns_edge_grants_owner_on_track():
    user = await _mk_user("owns_track")
    track = await _mk_track("T")
    await user.connect(track, edge=OWNS)
    assert await resolve_role(user.id, "track", track.id) == "owner"


@pytest.mark.asyncio
async def test_collab_role_returned_verbatim_on_track():
    owner = await _mk_user("collab_track_owner")
    track = await _mk_track("T")
    await owner.connect(track, edge=OWNS)

    for role in ("editor", "commenter", "viewer"):
        u = await _mk_user(f"collab_track_{role}")
        await u.connect(track, edge=COLLABORATES_ON, role=role)
        assert await resolve_role(u.id, "track", track.id) == role


@pytest.mark.asyncio
async def test_no_grant_no_role_on_track():
    stranger = await _mk_user("stranger_track")
    track = await _mk_track("T", visibility="private")
    assert await resolve_role(stranger.id, "track", track.id) is None


# ---------------------------------------------------------------------------
# App cascade onto Track
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_space_owner_cascades_to_track_as_editor():
    """Inherited 'owner' is capped to 'editor' on children (I-ROLE-02).

    Track-config authority NEVER cascades — must be granted directly
    per-resource. App owner inheriting onto a contained Track gets
    entry-CRUD authority ('editor') but cannot reshape the Track's
    schema/views/tags without an explicit direct 'admin' or 'owner'
    grant on that specific Track. Owner-only carve-outs (delete /
    collab mgmt / share-link mint) likewise never cascade.
    """
    sp_owner = await _mk_user("sp_owner_1")
    sp = await _mk_space("S")
    track = await _mk_track("T")
    await sp_owner.connect(sp, edge=OWNS)
    await sp.connect(track, edge=CONTAINS)
    assert await resolve_role(sp_owner.id, "track", track.id) == "editor"


@pytest.mark.asyncio
async def test_space_editor_cascades_to_track_as_editor():
    owner = await _mk_user("sp_ed_owner")
    editor = await _mk_user("sp_ed_editor")
    sp = await _mk_space("S")
    track = await _mk_track("T")
    await owner.connect(sp, edge=OWNS)
    await editor.connect(sp, edge=COLLABORATES_ON, role="editor")
    await sp.connect(track, edge=CONTAINS)
    assert await resolve_role(editor.id, "track", track.id) == "editor"


@pytest.mark.asyncio
async def test_space_viewer_cascades_to_track_as_viewer():
    owner = await _mk_user("sp_vw_owner")
    viewer = await _mk_user("sp_vw_viewer")
    sp = await _mk_space("S")
    track = await _mk_track("T")
    await owner.connect(sp, edge=OWNS)
    await viewer.connect(sp, edge=COLLABORATES_ON, role="viewer")
    await sp.connect(track, edge=CONTAINS)
    assert await resolve_role(viewer.id, "track", track.id) == "viewer"


@pytest.mark.asyncio
async def test_space_commenter_cascades_to_track_as_commenter():
    owner = await _mk_user("sp_cm_owner")
    commenter = await _mk_user("sp_cm_commenter")
    sp = await _mk_space("S")
    track = await _mk_track("T")
    await owner.connect(sp, edge=OWNS)
    await commenter.connect(sp, edge=COLLABORATES_ON, role="commenter")
    await sp.connect(track, edge=CONTAINS)
    assert await resolve_role(commenter.id, "track", track.id) == "commenter"


# ---------------------------------------------------------------------------
# Visibility field is inert (forward-compat slot only)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_track_visibility_field_does_not_block_cascade():
    """``Track.visibility="private"`` opts out of workspace-wide visibility
    grants on the parent App but does not block App ``COLLABORATES_ON``
    cascade. Per-user ``EXCLUDED_FROM`` is the per-user override.
    """
    owner = await _mk_user("priv_track_owner")
    sp_collab = await _mk_user("priv_track_sp_collab")
    sp = await _mk_space("S")
    track = await _mk_track("T", visibility="private")
    await owner.connect(sp, edge=OWNS)
    await sp_collab.connect(sp, edge=COLLABORATES_ON, role="editor")
    await sp.connect(track, edge=CONTAINS)
    assert await resolve_role(sp_collab.id, "track", track.id) == "editor"


@pytest.mark.asyncio
async def test_direct_grant_on_track_with_private_visibility_still_works():
    """Direct grants behave normally regardless of track visibility."""
    user = await _mk_user("priv_track_direct")
    track = await _mk_track("T", visibility="private")
    await user.connect(track, edge=COLLABORATES_ON, role="viewer")
    assert await resolve_role(user.id, "track", track.id) == "viewer"


# ---------------------------------------------------------------------------
# Entry cascade and visibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entry_inherits_from_track_collaborator():
    owner = await _mk_user("entry_inh_owner")
    collab = await _mk_user("entry_inh_collab")
    track = await _mk_track("T")
    await owner.connect(track, edge=OWNS)
    await collab.connect(track, edge=COLLABORATES_ON, role="editor")
    entry = await _mk_entry("E", track_id=track.id)
    await track.connect(entry, edge=CONTAINS)
    assert await resolve_role(collab.id, "entry", entry.id) == "editor"


@pytest.mark.asyncio
async def test_entry_inherits_from_space_cascade_chain():
    """Entry → Track → App cascade walks the full chain."""
    sp_collab = await _mk_user("entry_sp_chain")
    sp = await _mk_space("S")
    track = await _mk_track("T")
    await sp_collab.connect(sp, edge=COLLABORATES_ON, role="viewer")
    await sp.connect(track, edge=CONTAINS)
    entry = await _mk_entry("E", track_id=track.id)
    await track.connect(entry, edge=CONTAINS)
    assert await resolve_role(sp_collab.id, "entry", entry.id) == "viewer"


@pytest.mark.asyncio
async def test_entry_visibility_field_does_not_block_cascade():
    """``Entry.visibility="private"`` is a forward-compat slot only — the
    resolver does not consult it. Entry access follows parent Track unless
    a per-user ``EXCLUDED_FROM`` edge intervenes.
    """
    track_collab = await _mk_user("priv_entry_collab")
    track = await _mk_track("T")
    await track_collab.connect(track, edge=COLLABORATES_ON, role="editor")
    entry = await _mk_entry("E", track_id=track.id, visibility="private")
    await track.connect(entry, edge=CONTAINS)
    assert await resolve_role(track_collab.id, "entry", entry.id) == "editor"


@pytest.mark.asyncio
async def test_entry_exclusion_blocks_cascade_even_when_visibility_inherit():
    """``EXCLUDED_FROM`` is the only cascade-override under the new model."""
    user = await _mk_user("entry_excl_cascade")
    track = await _mk_track("T")
    await user.connect(track, edge=COLLABORATES_ON, role="editor")
    entry = await _mk_entry("E", track_id=track.id, visibility="inherit")
    await track.connect(entry, edge=CONTAINS)
    await user.connect(entry, edge=EXCLUDED_FROM)
    assert await resolve_role(user.id, "entry", entry.id) is None


# ---------------------------------------------------------------------------
# Exclusion blocks inheritance only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exclusion_blocks_space_cascade_on_track():
    user = await _mk_user("excl_track")
    sp = await _mk_space("S")
    track = await _mk_track("T")
    await user.connect(sp, edge=COLLABORATES_ON, role="editor")
    await sp.connect(track, edge=CONTAINS)
    await user.connect(track, edge=EXCLUDED_FROM)
    assert await resolve_role(user.id, "track", track.id) is None


@pytest.mark.asyncio
async def test_exclusion_does_not_block_direct_grant():
    user = await _mk_user("excl_direct")
    track = await _mk_track("T")
    await user.connect(track, edge=COLLABORATES_ON, role="editor")
    await user.connect(track, edge=EXCLUDED_FROM)
    assert await resolve_role(user.id, "track", track.id) == "editor"


@pytest.mark.asyncio
async def test_entry_exclusion_blocks_track_cascade():
    user = await _mk_user("excl_entry")
    track = await _mk_track("T")
    await user.connect(track, edge=COLLABORATES_ON, role="editor")
    entry = await _mk_entry("E", track_id=track.id)
    await track.connect(entry, edge=CONTAINS)
    await user.connect(entry, edge=EXCLUDED_FROM)
    assert await resolve_role(user.id, "entry", entry.id) is None


@pytest.mark.asyncio
async def test_space_exclusion_blocks_nothing_for_direct_space_collab():
    """A direct space collaborator with an exclusion on App itself: the
    direct grant wins (exclusion blocks inheritance only). Tests that
    EXCLUDED_FROM is correctly scoped per-resource."""
    user = await _mk_user("excl_space_self")
    sp = await _mk_space("S")
    await user.connect(sp, edge=COLLABORATES_ON, role="editor")
    await user.connect(sp, edge=EXCLUDED_FROM)
    assert await resolve_role(user.id, "app", sp.id) == "editor"


# ---------------------------------------------------------------------------
# Workspace membership does NOT cascade (rule 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workspace_member_no_implicit_space_access():
    from app.models.edges import IS_MEMBER_OF

    user = await _mk_user("ws_member_no_cascade")
    ws = await _mk_workspace("W")
    sp = await _mk_space("S", workspace_id=ws.id)
    await user.connect(ws, edge=IS_MEMBER_OF, role="member")
    # Member of workspace but no explicit space grant → no access.
    assert await resolve_role(user.id, "app", sp.id) is None


@pytest.mark.asyncio
async def test_workspace_member_no_implicit_track_access():
    from app.models.edges import IS_MEMBER_OF

    user = await _mk_user("ws_member_no_track")
    ws = await _mk_workspace("W")
    track = await _mk_track("T", workspace_id=ws.id)
    await user.connect(ws, edge=IS_MEMBER_OF, role="member")
    assert await resolve_role(user.id, "track", track.id) is None


@pytest.mark.asyncio
async def test_workspace_membership_resolved_at_workspace_resource():
    """WorkApp role itself surfaces; just doesn't cascade to children."""
    from app.models.edges import IS_MEMBER_OF

    user = await _mk_user("ws_role_self")
    ws = await _mk_workspace("W")
    await user.connect(ws, edge=IS_MEMBER_OF, role="member")
    assert await resolve_role(user.id, "workspace", ws.id) == "editor"


@pytest.mark.asyncio
async def test_workspace_membership_gate_blocks_direct_app_grant_after_removal():
    owner = await _mk_user("ws_gate_app_owner")
    collaborator = await _mk_user("ws_gate_app_collab")
    ws = await _mk_workspace("W")
    app_node = await _mk_space("S", workspace_id=ws.id)
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner")
    await collaborator.connect(ws, edge=IS_MEMBER_OF, role="guest")
    await collaborator.connect(app_node, edge=COLLABORATES_ON, role="viewer")
    assert await resolve_role(collaborator.id, "app", app_node.id) == "viewer"

    ctx = await collaborator.get_context()
    member_edges = await ctx.find_edges_between(
        collaborator.id, ws.id, edge_class=IS_MEMBER_OF
    )
    for edge in member_edges:
        await edge.delete()
    assert await resolve_role(collaborator.id, "app", app_node.id) is None


@pytest.mark.asyncio
async def test_workspace_membership_gate_blocks_entry_grant_after_removal():
    owner = await _mk_user("ws_gate_entry_owner")
    collaborator = await _mk_user("ws_gate_entry_collab")
    ws = await _mk_workspace("W")
    track = await _mk_track("T", workspace_id=ws.id)
    entry = await _mk_entry("E", track_id=track.id)
    await track.connect(entry, edge=CONTAINS)
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner")
    await collaborator.connect(ws, edge=IS_MEMBER_OF, role="guest")
    await collaborator.connect(entry, edge=COLLABORATES_ON, role="viewer")
    assert await resolve_role(collaborator.id, "entry", entry.id) == "viewer"

    ctx = await collaborator.get_context()
    member_edges = await ctx.find_edges_between(
        collaborator.id, ws.id, edge_class=IS_MEMBER_OF
    )
    for edge in member_edges:
        await edge.delete()
    assert await resolve_role(collaborator.id, "entry", entry.id) is None


# ---------------------------------------------------------------------------
# Personal-workspace owner short-circuit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_personal_workspace_owner_short_circuit():
    user = await _mk_user("personal_owner")
    ws = await Workspace.create(kind="personal", name="Personal")
    await user.connect(ws, edge=IS_MEMBER_OF, role="owner")
    assert await resolve_role(user.id, "workspace", ws.id) == "owner"


@pytest.mark.asyncio
async def test_personal_workspace_non_owner_no_access():
    owner = await _mk_user("p_owner")
    other = await _mk_user("p_other")
    ws = await Workspace.create(kind="personal", name="Personal")
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner")
    assert await resolve_role(other.id, "workspace", ws.id) is None


# ---------------------------------------------------------------------------
# Standalone Track (no parent) — no cascade target
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_standalone_track_inherit_yields_no_role_without_direct_grant():
    """Track with visibility=inherit but no App parent has no one to inherit
    from; resolver returns None (unless the user has a direct grant)."""
    user = await _mk_user("standalone_inh")
    track = await _mk_track("T", visibility="inherit")
    assert await resolve_role(user.id, "track", track.id) is None


# ---------------------------------------------------------------------------
# Direct grants take precedence at each level
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_track_direct_grant_beats_space_inheritance():
    """If the user has a direct COLLABORATES_ON on the Track, that role wins
    even when the App would grant something else."""
    user = await _mk_user("direct_beats_cascade")
    sp = await _mk_space("S")
    track = await _mk_track("T")
    await user.connect(sp, edge=COLLABORATES_ON, role="viewer")
    await sp.connect(track, edge=CONTAINS)
    # Direct editor grant on Track — should win over inherited viewer.
    await user.connect(track, edge=COLLABORATES_ON, role="editor")
    assert await resolve_role(user.id, "track", track.id) == "editor"


@pytest.mark.asyncio
async def test_entry_direct_grant_beats_track_inheritance():
    user = await _mk_user("entry_direct_beats")
    track = await _mk_track("T")
    await user.connect(track, edge=COLLABORATES_ON, role="viewer")
    entry = await _mk_entry("E", track_id=track.id)
    await track.connect(entry, edge=CONTAINS)
    await user.connect(entry, edge=COLLABORATES_ON, role="editor")
    assert await resolve_role(user.id, "entry", entry.id) == "editor"


# ---------------------------------------------------------------------------
# Owner-direct beats both cascade and exclusion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owns_beats_exclusion():
    user = await _mk_user("owns_beats_excl")
    track = await _mk_track("T")
    await user.connect(track, edge=OWNS)
    await user.connect(track, edge=EXCLUDED_FROM)
    assert await resolve_role(user.id, "track", track.id) == "owner"


# ---------------------------------------------------------------------------
# Unknown resource type / non-existent ID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_resource_type_returns_none():
    user = await _mk_user("unknown_type")
    assert await resolve_role(user.id, "view", "irrelevant") is None
    assert await resolve_role(user.id, "garbage", "irrelevant") is None


@pytest.mark.asyncio
async def test_nonexistent_resource_id_returns_none():
    user = await _mk_user("missing_resource")
    assert await resolve_role(user.id, "track", "n.Track.does-not-exist") is None
    assert await resolve_role(user.id, "entry", "n.Entry.does-not-exist") is None


@pytest.mark.asyncio
async def test_missing_user_returns_none():
    track = await _mk_track("T")
    assert await resolve_role("rr_user_nobody", "track", track.id) is None


# ---------------------------------------------------------------------------
# Inherited owner caps to editor across two-level chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_space_owner_to_entry_caps_at_editor_each_level():
    """App owner → Track (editor) → Entry (editor). Cap applies at each
    inheritance hop, never promotes back to owner / admin (I-ROLE-02).
    Track-config authority (admin tier) MUST be granted directly per
    resource — never inherited."""
    owner = await _mk_user("cap_owner_chain")
    sp = await _mk_space("S")
    track = await _mk_track("T")
    await owner.connect(sp, edge=OWNS)
    await sp.connect(track, edge=CONTAINS)
    entry = await _mk_entry("E", track_id=track.id)
    await track.connect(entry, edge=CONTAINS)
    assert await resolve_role(owner.id, "track", track.id) == "editor"
    assert await resolve_role(owner.id, "entry", entry.id) == "editor"
