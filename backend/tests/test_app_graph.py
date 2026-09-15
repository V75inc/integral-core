"""Invariants for the Integral app graph shell + per-workspace branches."""

from datetime import datetime

import pytest
from jvspatial.core import Edge

from app.models.edges import CATALOGS, CONTAINS
from app.models.nodes import (
    APP_NODE_ID,
    CONTENT_PROFILES_REGISTRY_ID,
    USERS_REGISTRY_ID,
    WORKSPACES_REGISTRY_ID,
    App,
    Apps,
    ChatThread,
    ChatThreads,
    ContentProfile,
    ContentProfiles,
    IntegralApp,
    Track,
    Tracks,
    User,
    Users,
    Views,
    Workspace,
    Workspaces,
)
from app.services.app_graph import (
    _workspace_branch_id,
    catalog_app,
    catalog_chat_thread,
    catalog_track,
    catalog_workspace,
    ensure_integral_app_graph,
    ensure_workspace_branches,
    get_or_create_views_registry_for_content_profile,
    get_track_attached_content_profile,
)


@pytest.mark.asyncio
async def test_ensure_creates_app_and_registries():
    await ensure_integral_app_graph(include_library=False)
    app = await IntegralApp.get(APP_NODE_ID)
    assert app is not None
    assert await Users.get(USERS_REGISTRY_ID)
    assert await Workspaces.get(WORKSPACES_REGISTRY_ID)
    assert await ContentProfiles.get(CONTENT_PROFILES_REGISTRY_ID)


@pytest.mark.asyncio
async def test_ensure_workspace_branches_creates_three_per_workspace():
    await ensure_integral_app_graph(include_library=False)
    now = datetime.now().isoformat()
    ws = await Workspace.create(
        kind="organization",
        name="Branch Org",
        name_fold="branch org",
        created_at=now,
        updated_at=now,
    )
    sp_b, tr_b, ct_b = await ensure_workspace_branches(ws)
    assert isinstance(sp_b, Apps)
    assert isinstance(tr_b, Tracks)
    assert isinstance(ct_b, ChatThreads)
    assert sp_b.id == _workspace_branch_id(ws.id, "apps")
    assert tr_b.id == _workspace_branch_id(ws.id, "tracks")
    assert ct_b.id == _workspace_branch_id(ws.id, "chat_threads")
    # Workspace—CONTAINS→Branch edges exist.
    contained = await ws.nodes(
        edge=[CONTAINS],
        node=["Apps", "Tracks", "ChatThreads"],
    )
    assert {b.id for b in contained} >= {sp_b.id, tr_b.id, ct_b.id}
    # Idempotent — second call returns the same nodes, no duplicates.
    sp_b2, tr_b2, ct_b2 = await ensure_workspace_branches(ws)
    assert (sp_b2.id, tr_b2.id, ct_b2.id) == (sp_b.id, tr_b.id, ct_b.id)
    contained_again = await ws.nodes(
        edge=[CONTAINS],
        node=["Apps", "Tracks", "ChatThreads"],
    )
    assert len(contained_again) == 3


@pytest.mark.asyncio
async def test_catalog_space_writes_under_workspace_branch():
    await ensure_integral_app_graph(include_library=False)
    now = datetime.now().isoformat()
    ws = await Workspace.create(
        kind="organization",
        name="Cat Sp",
        name_fold="cat sp",
        created_at=now,
        updated_at=now,
    )
    await catalog_workspace(ws)
    sp = await App.create(
        name="S",
        name_fold="s",
        owner_user_id="u",
        workspace_id=ws.id,
        created_at=now,
        updated_at=now,
    )
    await catalog_app(sp)
    branch = await Apps.get(_workspace_branch_id(ws.id, "apps"))
    assert branch is not None
    cataloged = await branch.nodes(edge=[CATALOGS], node=["WorkspaceApp"])
    assert any(s.id == sp.id for s in cataloged)


@pytest.mark.asyncio
async def test_catalog_track_writes_under_workspace_branch():
    await ensure_integral_app_graph(include_library=False)
    u = await User.create(user_id="graph_u1", display_name="G")
    now = datetime.now().isoformat()
    ws = await Workspace.create(
        kind="organization",
        name="Cat Tr",
        name_fold="cat tr",
        created_at=now,
        updated_at=now,
    )
    await catalog_workspace(ws)
    t = await Track.create(
        title="T1",
        owner_id=u.id,
        workspace_id=ws.id,
        created_at=now,
        updated_at=now,
    )
    await catalog_track(t)
    branch = await Tracks.get(_workspace_branch_id(ws.id, "tracks"))
    assert branch is not None
    cataloged = await branch.nodes(edge=[CATALOGS], node=["Track"])
    assert any(x.id == t.id for x in cataloged)
    # ContentProfile + Views registry still set up.
    cp = await get_track_attached_content_profile(t)
    assert cp is not None
    vreg = await get_or_create_views_registry_for_content_profile(cp, track=t)
    assert isinstance(vreg, Views)
    children = await cp.nodes(edge=[Edge], node=["Views"])
    assert len(children) >= 1


@pytest.mark.asyncio
async def test_ensure_track_content_profile_default_bootstraps_post_and_feed():
    """Non-templated tracks (no template_id) get the generic Post entry type +
    Feed view — the historical default-bootstrap behavior."""
    await ensure_integral_app_graph(include_library=False)
    u = await User.create(user_id="graph_u_bootstrap", display_name="G")
    now = datetime.now().isoformat()
    ws = await Workspace.create(
        kind="organization",
        name="Bootstrap WS",
        name_fold="bootstrap ws",
        created_at=now,
        updated_at=now,
    )
    await catalog_workspace(ws)
    t = await Track.create(
        title="Untemplated",
        owner_id=u.id,
        workspace_id=ws.id,
        created_at=now,
        updated_at=now,
    )
    await catalog_track(t)

    from app.models.nodes import EntryType

    entry_types = await EntryType.find({"context.track_id": t.id})
    assert any(et.name == "Post" for et in entry_types)


@pytest.mark.asyncio
async def test_ensure_track_content_profile_skips_bootstrap_for_templated_track():
    """A track carrying template_id (bundle-prescribed) must NOT get the
    generic Post entry type / Feed view bootstrap, even when the caller
    doesn't explicitly pass skip_default_bootstrap=True.

    catalog_track (called for every track, including inside
    provision_prescribed_tracks_from_app_manifest's per-track loop) is
    itself one of the unguarded call sites — it calls
    ensure_track_attached_content_profile(track) with no explicit flag.
    Before this fix, that meant every templated track got bootstrapped
    with Post + Feed here, and the later manifest merge only ever ADDED
    its real entry types on top instead of replacing the contamination —
    exactly the June 23 QA bug where Content Factory's Performance track
    showed both "Performance Record" and a stray "Post" in the New Record
    entry-type selector."""
    await ensure_integral_app_graph(include_library=False)
    u = await User.create(user_id="graph_u_templated", display_name="G")
    now = datetime.now().isoformat()
    ws = await Workspace.create(
        kind="organization",
        name="Templated WS",
        name_fold="templated ws",
        created_at=now,
        updated_at=now,
    )
    await catalog_workspace(ws)
    t = await Track.create(
        title="Templated",
        owner_id=u.id,
        workspace_id=ws.id,
        template_id="performance",
        created_at=now,
        updated_at=now,
    )
    await catalog_track(t)

    from app.models.nodes import EntryType

    entry_types = await EntryType.find({"context.track_id": t.id})
    assert not any(et.name == "Post" for et in entry_types)


@pytest.mark.asyncio
async def test_ensure_track_content_profile_templated_shell_suppresses_feed_fallback():
    """The empty placeholder shell created for a templated track must itself
    declare suppress_feed_fallback: true — not just skip the Post/Feed
    materialization above.

    Same class of bug as the Post/Feed skip above, one level further down:
    _merge_track_tier_into_manifest (the manifest-merge step that applies
    the track's real content afterward) compiles this shell's RAW manifest
    to read back "what views does the target already have" before unioning
    in the real content. Compiling a shell that stays silent on
    suppress_feed_fallback invents a default Feed view via the normal
    no-opinion fallback — and since that union only ever ADDS keys the
    incoming tier doesn't already have, a real manifest declaring
    suppress_feed_fallback: true could never get rid of it. Found live: a
    bundle-prescribed Settings track applied with suppress_feed_fallback:
    true still came back with a visible Feed tab after install."""
    await ensure_integral_app_graph(include_library=False)
    u = await User.create(user_id="graph_u_templated_feed", display_name="G")
    now = datetime.now().isoformat()
    ws = await Workspace.create(
        kind="organization",
        name="Templated Feed WS",
        name_fold="templated feed ws",
        created_at=now,
        updated_at=now,
    )
    await catalog_workspace(ws)
    t = await Track.create(
        title="Templated Feed",
        owner_id=u.id,
        workspace_id=ws.id,
        template_id="settings",
        created_at=now,
        updated_at=now,
    )
    await catalog_track(t)

    cp = await get_track_attached_content_profile(t)
    assert cp is not None
    track_tier = (cp.manifest or {}).get("track") or {}
    assert track_tier.get("suppress_feed_fallback") is True
    assert track_tier.get("views") == []


@pytest.mark.asyncio
async def test_catalog_chat_thread_under_workspace_branch():
    await ensure_integral_app_graph(include_library=False)
    now = datetime.now().isoformat()
    ws = await Workspace.create(
        kind="personal",
        name="Personal",
        name_fold="personal",
        created_at=now,
        updated_at=now,
    )
    await catalog_workspace(ws)
    thread = await ChatThread.create(
        user_id="u",
        workspace_id=ws.id,
        provider_id="jvagent",
        title="t",
        created_at=now,
        updated_at=now,
        last_message_at=now,
    )
    await catalog_chat_thread(thread)
    branch = await ChatThreads.get(_workspace_branch_id(ws.id, "chat_threads"))
    assert branch is not None
    cataloged = await branch.nodes(edge=[CATALOGS], node=["ChatThread"])
    assert any(x.id == thread.id for x in cataloged)


@pytest.mark.asyncio
async def test_users_registry_catalogs_user():
    await ensure_integral_app_graph(include_library=False)
    reg = await Users.get(USERS_REGISTRY_ID)
    u = await User.create(user_id="graph_u2", display_name="U2")
    from app.services.app_graph import catalog_user

    await catalog_user(u)
    linked = await reg.nodes(edge=[CATALOGS], node=["User"])
    assert any(x.id == u.id for x in linked)


@pytest.mark.asyncio
async def test_boot_sync_registers_bundle_and_is_idempotent(monkeypatch, tmp_path):
    bundle = tmp_path / "boot-bundle"
    bundle.mkdir()
    (bundle / "profile.yaml").write_text(
        "integral_profile_version: 3\n"
        "scope: track\n"
        "package:\n"
        "  slug: boot-bundle\n"
        "  name: Boot Bundle\n"
        "  version: 1.0.0\n"
        "track:\n"
        "  entry_types: []\n"
    )
    monkeypatch.setattr("app.services.content_profile_loader._PROFILES_ROOT", tmp_path)
    from app.services.content_profile_library_sync import (
        reset_library_profiles_cache_for_testing,
    )

    try:
        reset_library_profiles_cache_for_testing()

        await ensure_integral_app_graph(include_library=True)
        await ensure_integral_app_graph(include_library=True)

        rows = await ContentProfile.find({"context.metadata.slug": "boot-bundle"})
        if rows is None:
            found = []
        elif isinstance(rows, list):
            found = rows
        else:
            found = [rows]
        assert len(found) == 1
        assert found[0].name == "Boot Bundle"
    finally:
        reset_library_profiles_cache_for_testing()
