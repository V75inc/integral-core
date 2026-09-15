"""dispatch_tool propose path: stagers mint a StagedChange, never apply the write.

These exercise Task 5a: ``dispatch_tool`` for ``op_class == "propose"`` tools
routes through ``_dispatch_propose``, which calls the tool's ``ToolBinding.stager``
to map call-args -> ``create_staged_change`` kwargs, then mints a pending
``StagedChange`` under the dispatch ``principal_id`` (identity is NEVER from
args) with the dispatch ``scope`` bound via the ``current_scope_workspace_id``
ContextVar around the (possibly async) stager.

The defining property a propose dispatch MUST hold: it STAGES, it does not
APPLY. ``test_create_entry_propose_returns_staged_token`` proves this by
asserting the seeded track has no new entry after a ``integral_create_entry``
propose dispatch — only a user *bless* (out of scope here) runs the executor.

The inline bootstrap mirrors ``test_tooling_dispatch_read`` (no HTTP signup;
real route handlers seed the graph) so the propose path runs against the exact
substrate the read tools read.
"""

import pytest

from app.agentive.tooling.dispatch import ToolResult, dispatch_tool


async def _bootstrap_principal_and_track():
    """Create an AuthUser + User + personal workspace, then a track in it.

    Returns ``(auth_user_id, workspace_id, track_id)``. Mirrors
    ``test_tooling_dispatch_read._bootstrap_principal_and_track``.
    """
    from jvspatial.api.auth.models import UserCreate

    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.auth import _get_auth_service
    from app.api.tracks import create_track
    from app.models.nodes import User
    from app.services.app_graph import catalog_user
    from app.services.personal_workspace import ensure_personal_workspace

    auth_service = _get_auth_service()
    user_response = await auth_service.register_user(
        UserCreate(email="propose-test@example.com", password="testpassword123")
    )
    auth_user_id = user_response.id
    user_node = await User.create(user_id=auth_user_id, display_name="Propose Test")
    await catalog_user(user_node)
    ws = await ensure_personal_workspace(user_node)
    workspace_id = ws.id if ws else None

    created = await invoke_route_in_process(
        create_track,
        principal_id=auth_user_id,
        scope=workspace_id,
        title="Seeded Track",
        visibility="private",
        workspace_id=workspace_id,
    )
    assert not (isinstance(created, dict) and created.get("error")), created
    track_id = created["track"]["id"]
    return auth_user_id, workspace_id, track_id


async def _count_entries(auth_user_id, workspace_id, track_id) -> int:
    """Count entries in ``track_id`` via the already-wired read tool.

    ``integral_query_entries`` dispatches the ``list_entries`` route handler
    (``{"entries": [...], "total": ...}``). Used to prove a propose dispatch
    staged-but-did-not-apply: the count stays unchanged after the propose.
    """
    r = await dispatch_tool(
        "integral_query_entries",
        {"track_id": track_id},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert not r.is_error, r
    return len(r.data.get("entries", []))


@pytest.fixture(autouse=True)
def _reset_staging_store():
    """Clear the module-global in-memory staging store around each test."""
    from app.agentive.staging import _reset_for_tests

    _reset_for_tests()
    yield
    _reset_for_tests()


def _assert_staged(result: ToolResult, *, kind: str):
    """Assert ``result`` is a non-error ToolResult carrying a pending StagedChange."""
    assert not result.is_error, result
    assert result.data is not None
    assert result.data.get("_kind") == "staged_change", result.data
    assert result.data.get("token"), result.data
    assert result.data.get("state") == "pending", result.data
    assert result.data.get("kind") == kind, result.data


@pytest.mark.asyncio
async def test_create_entry_propose_returns_staged_token(
    bind_fresh_graph_context_for_async_tests,
):
    """integral_create_entry stages a create_entry — and creates NO entry.

    Dispatching the propose tool returns a pending StagedChange (token + kind +
    state). The track's entry count is UNCHANGED afterward, proving the dispatch
    staged the write rather than applying it (only a user bless runs the
    executor — out of scope for this test).
    """
    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()
    before = await _count_entries(auth_user_id, workspace_id, track_id)

    r = await dispatch_tool(
        "integral_create_entry",
        {"track_id": track_id, "title": "Drafted via propose", "text": "Q4 planning"},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    _assert_staged(r, kind="create_entry")

    after = await _count_entries(auth_user_id, workspace_id, track_id)
    assert after == before, f"propose must not apply: {before} -> {after}"


@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        (
            "integral_create_entry",
            {"text": "Follow up with Jane about the Q3 launch."},
        ),
        ("integral_author_profile", {}),
        ("integral_draft_new_profile", {"scope": "track"}),
        (
            "integral_share",
            {"resource_type": "workspace", "resource_id": "n.X.y", "email": "a@b.c"},
        ),
        (
            "integral_share",
            {"resource_type": "track", "resource_id": "TRACK", "role": "viewer"},
        ),
    ],
)
@pytest.mark.asyncio
async def test_propose_fails_closed_missing_required(
    bind_fresh_graph_context_for_async_tests,
    tool_name,
    args,
):
    """Missing required propose args fail closed — no StagedChange minted."""
    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()
    call_args = dict(args)
    if call_args.get("resource_id") == "TRACK":
        call_args["resource_id"] = track_id

    r = await dispatch_tool(
        tool_name,
        call_args,
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert r.is_error, r


@pytest.mark.asyncio
async def test_update_entry_propose_returns_staged_token(
    bind_fresh_graph_context_for_async_tests,
):
    """integral_update_entry stages an update_entry; payload carries the changes.

    Asserts the proposed ``status`` survives into the staged payload (it must:
    the ``update_entry`` handler accepts ``status`` and the executor forwards
    it). Regression guard for the silent-drop bug.
    """
    from app.agentive.staging import get_token

    auth_user_id, workspace_id, _track_id = await _bootstrap_principal_and_track()

    r = await dispatch_tool(
        "integral_update_entry",
        {"entry_id": "n.Entry.abc123", "title": "New title", "status": "done"},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    _assert_staged(r, kind="update_entry")
    dm = r.data["diff_machine"]
    assert dm.get("entry_id") == "n.Entry.abc123", dm
    assert dm.get("title") == "New title", dm
    assert dm.get("status") == "done", dm
    # The status reaches the staged payload (what the executor splats), not just
    # the human/machine diff — proving it is not silently dropped at stage time.
    sc = await get_token(r.data["token"])
    assert sc is not None
    assert sc.payload.get("status") == "done", sc.payload


@pytest.mark.asyncio
async def test_x_update_entry_forwards_status_to_handler(
    bind_fresh_graph_context_for_async_tests,
):
    """The bless-time executor forwards ``status`` to the update_entry handler.

    Patches ``app.api.entries.update_entry`` with a capture stub and runs
    ``_x_update_entry`` so we observe the exact kwargs the handler receives:
    ``status`` must be present (Fix 1 — silent-drop on bless). Forwarding is
    additive: a payload WITHOUT ``status`` must not introduce a ``status`` kwarg.
    """
    import app.api.entries as entries_mod
    from app.agentive.staging_executors import _x_update_entry

    captured: dict = {}

    async def _fake_update_entry(request, **kwargs):
        captured.clear()
        captured.update(kwargs)
        return {"ok": True}

    auth_user_id, _workspace_id, _track_id = await _bootstrap_principal_and_track()

    orig = entries_mod.update_entry
    entries_mod.update_entry = _fake_update_entry  # type: ignore[assignment]
    try:
        # status present -> forwarded.
        await _x_update_entry(
            auth_user_id,
            {"entry_id": "n.Entry.abc123", "title": "T", "status": "done"},
        )
        assert captured.get("entry_id") == "n.Entry.abc123", captured
        assert captured.get("status") == "done", captured

        # status absent -> no status kwarg introduced (additive, no regression).
        await _x_update_entry(
            auth_user_id,
            {"entry_id": "n.Entry.abc123", "title": "T"},
        )
        assert "status" not in captured, captured
    finally:
        entries_mod.update_entry = orig  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_create_track_propose_returns_staged_token(
    bind_fresh_graph_context_for_async_tests,
):
    """integral_create_track stages a create_track; no track listed until bless."""
    auth_user_id, workspace_id, _track_id = await _bootstrap_principal_and_track()

    r = await dispatch_tool(
        "integral_create_track",
        {"title": "Proposed Track", "visibility": "private"},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    _assert_staged(r, kind="create_track")

    # The proposed track is NOT created — list_tracks does not include it.
    lr = await dispatch_tool(
        "integral_list_tracks", {}, principal_id=auth_user_id, scope=workspace_id
    )
    assert not lr.is_error, lr
    titles = [t.get("title") for t in lr.data.get("tracks", [])]
    assert "Proposed Track" not in titles, titles


@pytest.mark.asyncio
async def test_create_app_track_propose_carries_app_id(
    bind_fresh_graph_context_for_async_tests,
):
    """integral_create_app_track stages a create_track whose payload carries app_id."""
    auth_user_id, workspace_id, _track_id = await _bootstrap_principal_and_track()

    r = await dispatch_tool(
        "integral_create_app_track",
        {"app_id": "n.WorkspaceApp.zzz", "title": "Track in App"},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    _assert_staged(r, kind="create_track")
    assert r.data["diff_machine"].get("app_id") == "n.WorkspaceApp.zzz", r.data


@pytest.mark.asyncio
async def test_modify_profile_subkind(bind_fresh_graph_context_for_async_tests):
    """integral_modify_profile computes a modify_profile.<action> sub-kind from args."""
    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()

    r = await dispatch_tool(
        "integral_modify_profile",
        {"action": "add_entry_type", "track_id": track_id, "name": "Meeting"},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    _assert_staged(r, kind="modify_profile.add_entry_type")
    assert r.data["diff_machine"].get("action") == "add_entry_type", r.data


@pytest.mark.asyncio
async def test_modify_profile_invalid_action_fails_closed(
    bind_fresh_graph_context_for_async_tests,
):
    """An unknown modify_profile action fails closed (no token minted)."""
    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()

    r = await dispatch_tool(
        "integral_modify_profile",
        {"action": "frobnicate", "track_id": track_id},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert r.is_error, r


@pytest.mark.parametrize(
    ("tool_name", "kind", "args"),
    [
        (
            "integral_propose_profile_revision",
            "propose_profile_revision",
            {
                "draft_id": "n.ContentProfile.draft1",
                "operations": [{"op": "add_entry_type", "name": "Note"}],
            },
        ),
        (
            "integral_publish_profile_draft",
            "publish_profile_draft",
            {"draft_id": "n.ContentProfile.draft1"},
        ),
        (
            "integral_discard_profile_draft",
            "discard_profile_draft",
            {"draft_id": "n.ContentProfile.draft1"},
        ),
        (
            "integral_apply_profile_to_track",
            "apply_library_profile",
            {
                "track_id": "TRACK",
                "library_cp_id": "n.ContentProfile.lib1",
            },
        ),
        (
            "integral_author_profile",
            "author_profile",
            {
                "description": "App for tracking records and items",
                "scope": "track",
            },
        ),
    ],
)
@pytest.mark.asyncio
async def test_profile_propose_tools_stage(
    bind_fresh_graph_context_for_async_tests,
    tool_name,
    kind,
    args,
):
    """Profile-family propose tools mint a pending StagedChange with expected kind."""
    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()
    call_args = dict(args)
    if call_args.get("track_id") == "TRACK":
        call_args["track_id"] = track_id

    r = await dispatch_tool(
        tool_name,
        call_args,
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    _assert_staged(r, kind=kind)
    dm = r.data["diff_machine"]
    if tool_name == "integral_propose_profile_revision":
        assert dm.get("draft_id") == "n.ContentProfile.draft1", dm
        assert dm.get("operations") == args["operations"], dm
    elif tool_name == "integral_apply_profile_to_track":
        assert dm.get("library_profile_id") == "n.ContentProfile.lib1", dm
        assert dm.get("track_id") == track_id, dm
    elif tool_name == "integral_author_profile":
        assert dm.get("description"), dm


# --------------------------------------------------------------------------- #
# T5b — add_comment / create_app / draft_new_profile staging executors.
# Each proves: dispatch stages (token + kind) AND does NOT apply the mutation.
# --------------------------------------------------------------------------- #
async def _seed_entry(auth_user_id, workspace_id, track_id):
    """Seed an entry in ``track_id`` via the real create_entry handler."""
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.entries import create_entry

    created = await invoke_route_in_process(
        create_entry,
        principal_id=auth_user_id,
        scope=workspace_id,
        track_id=track_id,
        title="Seeded Entry",
    )
    assert not (isinstance(created, dict) and created.get("error")), created
    return created["entry"]["id"]


async def _count_comments(auth_user_id, workspace_id, entry_id) -> int:
    """Count comments on ``entry_id`` via the wired ``integral_list_comments`` read."""
    r = await dispatch_tool(
        "integral_list_comments",
        {"entry_id": entry_id},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert not r.is_error, r
    return len(r.data.get("comments", []))


async def _app_titles(auth_user_id, workspace_id) -> list:
    """Return the names of apps visible via the wired ``integral_list_apps`` read."""
    r = await dispatch_tool(
        "integral_list_apps", {}, principal_id=auth_user_id, scope=workspace_id
    )
    assert not r.is_error, r
    apps = r.data.get("apps", [])
    return [a.get("name") for a in apps]


async def _draft_count(auth_user_id, workspace_id) -> int:
    """Count library content profiles visible via ``integral_list_profiles`` read."""
    r = await dispatch_tool(
        "integral_list_profiles", {}, principal_id=auth_user_id, scope=workspace_id
    )
    assert not r.is_error, r
    data = r.data
    # list_library_content_profiles returns {"content_profiles"|"profiles": [...]}.
    profiles = (
        data.get("content_profiles") or data.get("profiles") or data.get("items") or []
    )
    return len(profiles)


@pytest.mark.asyncio
async def test_add_comment_propose_returns_staged_token_no_comment(
    bind_fresh_graph_context_for_async_tests,
):
    """integral_add_comment stages an add_comment — and posts NO comment.

    The seeded entry's comment count is unchanged after the propose dispatch,
    proving the write is staged (runs only on a later user bless), not applied.
    """
    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()
    entry_id = await _seed_entry(auth_user_id, workspace_id, track_id)
    before = await _count_comments(auth_user_id, workspace_id, entry_id)

    r = await dispatch_tool(
        "integral_add_comment",
        {"entry_id": entry_id, "text": "Looks good to me"},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    _assert_staged(r, kind="add_comment")
    dm = r.data["diff_machine"]
    assert dm.get("entry_id") == entry_id, dm
    assert dm.get("body") == "Looks good to me", dm

    after = await _count_comments(auth_user_id, workspace_id, entry_id)
    assert after == before, f"propose must not apply: {before} -> {after}"


@pytest.mark.asyncio
async def test_x_add_comment_forwards_to_handler(
    bind_fresh_graph_context_for_async_tests,
):
    """The bless-time executor forwards entry_id + text (as ``text``) to the handler.

    Patches ``app.api.comments.create_comment`` with a capture stub and runs
    ``_x_add_comment`` so we observe the exact kwargs the handler receives: the
    payload's ``body`` maps to the handler's ``text`` kwarg; ``parent_id`` is
    forwarded only when present.
    """
    import app.api.comments as comments_mod
    from app.agentive.staging_executors import _x_add_comment

    captured: dict = {}

    async def _fake_create_comment(request, **kwargs):
        captured.clear()
        captured.update(kwargs)
        return {"comment": {"id": "n.Comment.x"}}

    auth_user_id, _ws, _track = await _bootstrap_principal_and_track()

    orig = comments_mod.create_comment
    comments_mod.create_comment = _fake_create_comment  # type: ignore[assignment]
    try:
        await _x_add_comment(
            auth_user_id,
            {"entry_id": "n.Entry.abc", "body": "hi", "parent_id": "n.Comment.p"},
        )
        assert captured.get("entry_id") == "n.Entry.abc", captured
        assert captured.get("text") == "hi", captured
        assert captured.get("parent_id") == "n.Comment.p", captured

        # parent_id absent -> not introduced.
        await _x_add_comment(auth_user_id, {"entry_id": "n.Entry.abc", "body": "hi"})
        assert "parent_id" not in captured, captured
    finally:
        comments_mod.create_comment = orig  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_create_app_propose_returns_staged_token_no_app(
    bind_fresh_graph_context_for_async_tests,
):
    """integral_create_app stages a create_app — and creates NO app.

    The proposed app name is absent from list_apps after the propose dispatch.
    """
    auth_user_id, workspace_id, _track_id = await _bootstrap_principal_and_track()
    before = await _app_titles(auth_user_id, workspace_id)
    assert "Proposed App" not in before

    r = await dispatch_tool(
        "integral_create_app",
        {"name": "Proposed App", "description": "A grouping"},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    _assert_staged(r, kind="create_app")
    assert r.data["diff_machine"].get("name") == "Proposed App", r.data

    after = await _app_titles(auth_user_id, workspace_id)
    assert "Proposed App" not in after, after


@pytest.mark.asyncio
async def test_create_app_drops_icon_arg(bind_fresh_graph_context_for_async_tests):
    """``icon`` is not a create_app handler kwarg — it is dropped from the payload."""
    auth_user_id, workspace_id, _track_id = await _bootstrap_principal_and_track()

    r = await dispatch_tool(
        "integral_create_app",
        {"name": "Iconic", "icon": "rocket"},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    _assert_staged(r, kind="create_app")
    from app.agentive.staging import get_token

    sc = await get_token(r.data["token"])
    assert sc is not None
    assert "icon" not in sc.payload, sc.payload
    assert "workspace_id" not in sc.payload, sc.payload  # scope is bound, not staged


@pytest.mark.asyncio
async def test_x_create_app_forwards_to_handler(
    bind_fresh_graph_context_for_async_tests,
):
    """The bless-time executor forwards name/description to the create_app handler."""
    import app.api.apps as apps_mod
    from app.agentive.staging_executors import _x_create_app

    captured: dict = {}

    async def _fake_create_app(request, **kwargs):
        captured.clear()
        captured.update(kwargs)
        return {"app": {"id": "n.WorkspaceApp.x"}}

    auth_user_id, _ws, _track = await _bootstrap_principal_and_track()

    orig = apps_mod.create_app
    apps_mod.create_app = _fake_create_app  # type: ignore[assignment]
    try:
        await _x_create_app(
            auth_user_id,
            {"name": "Acme", "description": "d", "visibility": "private"},
        )
        assert captured.get("name") == "Acme", captured
        assert captured.get("description") == "d", captured
        assert captured.get("visibility") == "private", captured
        # workspace_id never forced from the payload (handler resolves scope).
        assert "workspace_id" not in captured, captured
    finally:
        apps_mod.create_app = orig  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_draft_new_profile_propose_returns_staged_token_no_draft(
    bind_fresh_graph_context_for_async_tests,
):
    """integral_draft_new_profile stages a draft_new_profile — and creates NO draft.

    The library-profile count is unchanged after the propose dispatch.
    """
    auth_user_id, workspace_id, _track_id = await _bootstrap_principal_and_track()
    before = await _draft_count(auth_user_id, workspace_id)

    r = await dispatch_tool(
        "integral_draft_new_profile",
        {"profile_name": "Field Notes", "scope": "track"},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    _assert_staged(r, kind="draft_new_profile")
    dm = r.data["diff_machine"]
    assert dm.get("name") == "Field Notes", dm
    assert dm.get("scope") == "track", dm

    after = await _draft_count(auth_user_id, workspace_id)
    assert after == before, f"propose must not apply: {before} -> {after}"


@pytest.mark.asyncio
async def test_x_draft_new_profile_forwards_to_service(
    bind_fresh_graph_context_for_async_tests,
):
    """The bless-time executor forwards to create_empty_library_draft.

    Patches the service fn (imported lazily inside the executor) and runs
    ``_x_draft_new_profile`` so we observe the kwargs: name/scope forwarded;
    workspace_id falls back to the bound agent scope (set here directly).
    """
    import app.services.agent_profiles as ap_mod
    from app.agentive.staging_executors import _x_draft_new_profile
    from app.services.agent_scope import current_scope_workspace_id

    captured: dict = {}

    async def _fake_create_empty_library_draft(**kwargs):
        captured.clear()
        captured.update(kwargs)
        return {"draft_id": "n.ContentProfile.d", "name": kwargs.get("name")}

    auth_user_id, workspace_id, _track = await _bootstrap_principal_and_track()

    orig = ap_mod.create_empty_library_draft
    ap_mod.create_empty_library_draft = _fake_create_empty_library_draft  # type: ignore[assignment]
    token = current_scope_workspace_id.set(workspace_id)
    try:
        await _x_draft_new_profile(auth_user_id, {"name": "Notes", "scope": "track"})
        assert captured.get("user_id") == auth_user_id, captured
        assert captured.get("name") == "Notes", captured
        assert captured.get("scope") == "track", captured
        # workspace_id resolved from the bound agent scope.
        assert captured.get("workspace_id") == workspace_id, captured
    finally:
        current_scope_workspace_id.reset(token)
        ap_mod.create_empty_library_draft = orig  # type: ignore[assignment]


# --------------------------------------------------------------------------- #
# T5c — integral_share (collaborator-add, stage-and-bless).
# --------------------------------------------------------------------------- #
async def _count_track_collaborators(auth_user_id, workspace_id, track_id) -> int:
    """Count direct+inherited collaborators via the wired polymorphic access read."""
    r = await dispatch_tool(
        "integral_get_access",
        {"resource_type": "tracks", "id": track_id},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert not r.is_error, r
    direct = r.data.get("direct") or []
    return len(direct)


@pytest.mark.asyncio
async def test_share_propose_returns_staged_token_no_collaborator(
    bind_fresh_graph_context_for_async_tests,
):
    """integral_share stages a ``share`` — and adds NO collaborator.

    The seeded track's direct-collaborator count is unchanged after the propose
    dispatch, proving the grant is staged (runs only on a later user bless), not
    applied. Identity is the dispatch principal — a smuggled ``user_id`` arg
    never reaches the payload.
    """
    from app.agentive.staging import get_token

    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()
    before = await _count_track_collaborators(auth_user_id, workspace_id, track_id)

    r = await dispatch_tool(
        "integral_share",
        {
            "user_id": "ATTACKER",
            "resource_type": "track",
            "resource_id": track_id,
            "email": "someone@example.com",
            "role": "editor",
        },
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    _assert_staged(r, kind="share")
    dm = r.data["diff_machine"]
    assert dm.get("resource_type") == "track", dm
    assert dm.get("resource_id") == track_id, dm
    assert dm.get("role") == "editor", dm
    assert dm.get("email") == "someone@example.com", dm

    sc = await get_token(r.data["token"])
    assert sc is not None
    assert sc.user_id == auth_user_id  # identity from dispatch, not args
    assert "user_id" not in sc.payload  # stager mapped data only

    after = await _count_track_collaborators(auth_user_id, workspace_id, track_id)
    assert after == before, f"propose must not apply: {before} -> {after}"


@pytest.mark.asyncio
async def test_share_default_role_is_commenter(
    bind_fresh_graph_context_for_async_tests,
):
    """An unspecified share role defaults to commenter (I-ROLE-03)."""
    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()

    r = await dispatch_tool(
        "integral_share",
        {"resource_type": "app", "resource_id": "n.WorkspaceApp.x", "email": "a@b.c"},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    _assert_staged(r, kind="share")
    assert r.data["diff_machine"].get("role") == "commenter", r.data


@pytest.mark.asyncio
async def test_share_over_grant_role_fails_closed(
    bind_fresh_graph_context_for_async_tests,
):
    """A share role outside the manifest enum (e.g. ``owner``) fails closed.

    The manifest's ``integral_share`` declares ``role`` enum
    ``[viewer, commenter, editor]`` (role-cap, I-ROLE-03 / PC-3). The backing
    ``sharing.add_collaborator`` would otherwise accept ``owner``/``admin``; the
    stager clamps to the manifest enum and REJECTS the over-grant — no token is
    minted, and there is no silent downgrade to a lower role.
    """
    from app.agentive.staging import get_pending_for_user

    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()

    r = await dispatch_tool(
        "integral_share",
        {
            "resource_type": "track",
            "resource_id": track_id,
            "email": "someone@example.com",
            "role": "owner",
        },
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert r.is_error, r
    # Nothing staged — the over-grant never reaches a StagedChange.
    pending = await get_pending_for_user(auth_user_id)
    assert pending == [], pending


@pytest.mark.asyncio
async def test_share_editor_role_succeeds(
    bind_fresh_graph_context_for_async_tests,
):
    """An in-enum role (``editor``) stages successfully, carrying that exact role."""
    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()

    r = await dispatch_tool(
        "integral_share",
        {
            "resource_type": "track",
            "resource_id": track_id,
            "email": "someone@example.com",
            "role": "editor",
        },
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    _assert_staged(r, kind="share")
    assert r.data["diff_machine"].get("role") == "editor", r.data


@pytest.mark.asyncio
async def test_x_share_forwards_to_collaborator_handler(
    bind_fresh_graph_context_for_async_tests,
):
    """The bless-time executor resolves email→user and forwards to the handler.

    Patches the track collaborator handler with a capture stub and the
    email-resolution helper, then runs ``_x_share`` so we observe the exact
    kwargs: ``track_id`` (the path kwarg for resource_type=track),
    ``collaborator_user_id`` (the resolved id), and ``role``.
    """
    import app.api.tracks as tracks_mod
    import app.bootstrap_admin as bootstrap_mod
    from app.agentive.staging_executors import _x_share

    captured: dict = {}

    async def _fake_add_collaborator(request, **kwargs):
        captured.clear()
        captured.update(kwargs)
        return {"message": "Collaborator added successfully", **kwargs}

    class _FakeAuthUser:
        id = "auth-resolved-123"

    async def _fake_find_by_email(email):
        return _FakeAuthUser()

    auth_user_id, _ws, _track = await _bootstrap_principal_and_track()

    orig_handler = tracks_mod.add_collaborator
    orig_find = bootstrap_mod._find_user_by_email
    tracks_mod.add_collaborator = _fake_add_collaborator  # type: ignore[assignment]
    bootstrap_mod._find_user_by_email = _fake_find_by_email  # type: ignore[assignment]
    try:
        result = await _x_share(
            auth_user_id,
            {
                "resource_type": "track",
                "resource_id": "n.Track.abc",
                "email": "person@example.com",
                "role": "editor",
            },
        )
        assert not result.get("error"), result
        assert captured.get("track_id") == "n.Track.abc", captured
        assert captured.get("collaborator_user_id") == "auth-resolved-123", captured
        assert captured.get("role") == "editor", captured
    finally:
        tracks_mod.add_collaborator = orig_handler  # type: ignore[assignment]
        bootstrap_mod._find_user_by_email = orig_find  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_x_share_unresolvable_email_fails_closed(
    bind_fresh_graph_context_for_async_tests,
):
    """An email that resolves to no user fails closed in the executor (no write)."""
    import app.bootstrap_admin as bootstrap_mod
    from app.agentive.staging_executors import _x_share

    async def _fake_find_none(email):
        return None

    auth_user_id, _ws, _track = await _bootstrap_principal_and_track()

    orig_find = bootstrap_mod._find_user_by_email
    bootstrap_mod._find_user_by_email = _fake_find_none  # type: ignore[assignment]
    try:
        result = await _x_share(
            auth_user_id,
            {
                "resource_type": "track",
                "resource_id": "n.Track.abc",
                "email": "ghost@example.com",
            },
        )
        assert result.get("error"), result
        assert result.get("error_code") == "collaborator_not_found", result
    finally:
        bootstrap_mod._find_user_by_email = orig_find  # type: ignore[assignment]


# --------------------------------------------------------------------------- #
# T5c — integral_set_focus (ephemeral; direct-execute, NOT staged).
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_set_focus_no_context_id_fails_closed(
    bind_fresh_graph_context_for_async_tests,
):
    """set_focus dispatched without a context_id fails closed — and stages nothing.

    The external M2a dispatch contract carries only principal + workspace scope,
    no conversation/session id. So a set_focus dispatch (which mutates a SPECIFIC
    ConversationContext) cannot resolve a context and returns a clean error
    rather than a StagedChange. Crucially it is NOT a propose-staging path:
    no token is minted, so this is direct-execute fail-closed.
    """
    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()

    r = await dispatch_tool(
        "integral_set_focus",
        {"track_id": track_id},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert r.is_error, r
    assert r.error_code == "context_required", r
    # Nothing was staged — direct-execute mints no StagedChange.
    from app.agentive.staging import get_pending_for_user

    pending = await get_pending_for_user(auth_user_id)
    assert pending == [], pending


@pytest.mark.asyncio
async def test_set_focus_direct_execute_updates_focus(
    bind_fresh_graph_context_for_async_tests,
):
    """With a context_id (resident-supplied), set_focus runs IMMEDIATELY.

    Seeds a ConversationContext owned by the principal, then dispatches
    set_focus with the context_id. The dispatch returns a non-error result
    carrying the updated focus — proving the direct-execute path runs the
    service fn now (no staging / bless), and that the focus persisted.
    """
    from app.agentive.services.conversation_context import (
        get_or_create_conversation_context,
    )

    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()
    ctx = await get_or_create_conversation_context(
        user_id=auth_user_id,
        agent_type="integral",
        agent_conversation_id="conv-t5c-1",
        workspace_id=workspace_id,
    )

    r = await dispatch_tool(
        "integral_set_focus",
        {"context_id": ctx.id, "track_id": track_id},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert not r.is_error, r
    assert r.data.get("focused_track_id") == track_id, r.data
    assert r.data.get("context_id") == ctx.id, r.data

    # Persisted: re-reading the context shows the focus.
    from app.agentive.nodes import ConversationContext

    reread = await ConversationContext.get(ctx.id)
    assert getattr(reread, "focused_track_id", None) == track_id


@pytest.mark.asyncio
async def test_set_focus_rejects_foreign_context(
    bind_fresh_graph_context_for_async_tests,
):
    """A context owned by another principal cannot be mutated (PC-1 ownership gate)."""
    from app.agentive.services.conversation_context import (
        get_or_create_conversation_context,
    )

    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()
    # A context owned by a DIFFERENT user.
    ctx = await get_or_create_conversation_context(
        user_id="some-other-user",
        agent_type="integral",
        agent_conversation_id="conv-t5c-foreign",
        workspace_id=workspace_id,
    )

    r = await dispatch_tool(
        "integral_set_focus",
        {"context_id": ctx.id, "track_id": track_id},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert r.is_error, r
    assert r.error_code == "forbidden", r


@pytest.mark.asyncio
async def test_set_focus_rejects_unowned_context(
    bind_fresh_graph_context_for_async_tests,
):
    """An UNOWNED context (empty user_id) is rejected — the falsy-owner hole is closed.

    ``ConversationContext.user_id`` defaults to ``""``. The ownership gate used to
    short-circuit on a falsy ``owner_id``, letting ANY caller mutate a context
    whose owner was unset. The gate now fails CLOSED: an empty-owner context is
    forbidden for the caller, exactly like a foreign-owner context. (No legit
    flow produces an empty owner — the sole create path is auth-gated.)
    """
    from app.agentive.services.conversation_context import (
        get_or_create_conversation_context,
    )

    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()
    # Force an unowned context: create normally, then blank its owner to model
    # the anomaly the gate must reject.
    ctx = await get_or_create_conversation_context(
        user_id=auth_user_id,
        agent_type="integral",
        agent_conversation_id="conv-t5c-unowned",
        workspace_id=workspace_id,
    )
    ctx.user_id = ""
    await ctx.save()

    r = await dispatch_tool(
        "integral_set_focus",
        {"context_id": ctx.id, "track_id": track_id},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert r.is_error, r
    assert r.error_code == "forbidden", r


# --------------------------------------------------------------------------- #
# T5c — deferred orchestrations (workspace_setup / onboard_user).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "tool_name",
    ["integral_workspace_setup", "integral_onboard_user"],
)
@pytest.mark.asyncio
async def test_deferred_orchestrations_fail_closed(
    bind_fresh_graph_context_for_async_tests,
    tool_name,
):
    """workspace_setup / onboard_user are DEFERRED — propose dispatch fails closed.

    * integral_workspace_setup: NO legacy execute branch / orchestration service
      ever existed (only persona metadata) — nothing to stage.
    * integral_onboard_user: a genuine multi-turn state machine, not a single
      stageable mutation — resident-orchestration-only.

    Both carry an explicit ``stager=None`` binding, so _dispatch_propose returns
    ``not_implemented`` (a stable contract, not ``unknown_tool``). Pins the
    deferral and documents why neither is wired.
    """
    auth_user_id, workspace_id, _track_id = await _bootstrap_principal_and_track()

    r = await dispatch_tool(
        tool_name,
        {"template": "crm", "step": "start"},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert r.is_error, r
    assert r.error_code == "not_implemented", r


@pytest.mark.parametrize(
    ("tool_name", "kind", "args"),
    [
        ("integral_create_track", "create_track", {"title": "Identity check"}),
        (
            "integral_add_comment",
            "add_comment",
            {"user_id": "ATTACKER", "entry_id": "n.Entry.x", "text": "hi"},
        ),
        (
            "integral_create_app",
            "create_app",
            {"user_id": "ATTACKER", "name": "Sneaky App"},
        ),
        (
            "integral_draft_new_profile",
            "draft_new_profile",
            {"user_id": "ATTACKER", "profile_name": "Sneaky Profile"},
        ),
        (
            "integral_modify_profile",
            "modify_profile.add_entry_type",
            {"user_id": "ATTACKER", "action": "add_entry_type", "name": "Meeting"},
        ),
        (
            "integral_update_track",
            "update_track",
            {"user_id": "ATTACKER", "updates": {"title": "X"}},
        ),
        ("integral_delete_track", "delete_track", {"user_id": "ATTACKER"}),
        ("integral_delete_entry", "delete_entry", {"user_id": "ATTACKER"}),
        (
            "integral_save_view",
            "save_view",
            {"user_id": "ATTACKER", "name": "V", "view_type": "feed"},
        ),
    ],
)
@pytest.mark.asyncio
async def test_propose_does_not_inject_identity_into_payload(
    bind_fresh_graph_context_for_async_tests,
    tool_name,
    kind,
    args,
):
    """Stager maps data only — user_id is never smuggled into the staged payload."""
    from app.agentive.staging import get_token

    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()

    call_args = dict(args)
    if tool_name == "integral_modify_profile":
        call_args["track_id"] = track_id
    if tool_name in (
        "integral_update_track",
        "integral_delete_track",
        "integral_save_view",
    ):
        call_args["track_id"] = track_id
    if tool_name == "integral_delete_entry":
        entry_id = await _seed_entry(auth_user_id, workspace_id, track_id)
        call_args["entry_id"] = entry_id

    r = await dispatch_tool(
        tool_name,
        call_args,
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    _assert_staged(r, kind=kind)
    sc = await get_token(r.data["token"])
    assert sc is not None
    assert sc.user_id == auth_user_id
    assert "user_id" not in sc.payload


@pytest.mark.asyncio
async def test_propose_resets_scope_contextvar(
    bind_fresh_graph_context_for_async_tests,
):
    """The scope ContextVar is reset after a propose dispatch (no cross-call leak)."""
    from app.services.agent_scope import current_scope_workspace_id

    auth_user_id, workspace_id, _track_id = await _bootstrap_principal_and_track()

    assert current_scope_workspace_id.get() is None
    r = await dispatch_tool(
        "integral_create_track",
        {"title": "Scope check"},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert not r.is_error, r
    assert current_scope_workspace_id.get() is None


@pytest.mark.asyncio
async def test_execute_op_class_dispatches_direct_or_fails_closed(
    bind_fresh_graph_context_for_async_tests,
):
    """The lone execute-op-class tool dispatches via its direct binding.

    The manifest has exactly one ``op_class == "execute"`` tool
    (``integral_mark_notification_read``, status ``existing`` since the
    skills-editor gap-tool reconciliation). It is wired with a ``direct_ref``
    ToolBinding, so dispatch routes through ``_dispatch_direct`` — self-scoped,
    reversible, no staging. Execute tools WITHOUT a ``direct_ref`` still
    fail closed via ``_dispatch_execute_guard`` (covered by
    ``test_execute_guard_refuses_without_write_flag`` below). A dispatch
    against a nonexistent notification returns a clean error envelope, never
    an unhandled exception.
    """
    from app.agentive.tooling.bindings import TOOL_BINDINGS
    from app.agentive.tooling.manifest import load_manifest

    registry = load_manifest()
    execute_tools = [s.name for s in registry.values() if s.op_class == "execute"]
    assert execute_tools == ["integral_mark_notification_read"], execute_tools
    # The execute tool IS wired with a direct-dispatch binding.
    binding = TOOL_BINDINGS.get("integral_mark_notification_read")
    assert binding is not None and binding.direct_ref is not None, binding

    r = await dispatch_tool(
        "integral_mark_notification_read",
        {"notification_id": "n.Notification.x"},
        principal_id="some-user",
        scope=None,
    )
    # Bogus principal + notification id -> clean fail-closed envelope from the
    # direct path (no staged change, no crash).
    assert r.is_error, r
    assert r.error_code, r


@pytest.mark.asyncio
async def test_execute_guard_refuses_without_write_flag(
    bind_fresh_graph_context_for_async_tests,
):
    """The execute guard itself refuses absent a write flag (direct unit check).

    Exercises ``_dispatch_execute_guard`` against a synthetic execute spec so the
    guard's fail-closed default is covered even though no live execute tool is
    wired: no ``write``/``confirm`` flag -> ``write_scope_required``.
    """
    from types import SimpleNamespace

    from app.agentive.tooling.dispatch import _dispatch_execute_guard

    spec = SimpleNamespace(name="integral_synthetic_execute", op_class="execute")
    refused = _dispatch_execute_guard(spec, {})
    assert refused.is_error and refused.error_code == "write_scope_required", refused
    # With a write flag the guard passes the scope check (still unwired -> not_implemented).
    flagged = _dispatch_execute_guard(spec, {"write": True})
    assert flagged.is_error and flagged.error_code == "not_implemented", flagged


# --------------------------------------------------------------------------- #
# Task 1 — session_id / interaction_id threading + no-stage stager escape.
# dispatch_tool threads session/interaction into _dispatch_propose ->
# create_staged_change so the resident's [SYSTEM:STAGING-RESOLVED] closure
# marker + session autonomy-grant keep working post-manifest. The no-stage
# escape lets a stager signal "don't stage, return this data" (a later task's
# file_content unresolved branch) — minting NO StagedChange.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_propose_threads_session_and_interaction(
    bind_fresh_graph_context_for_async_tests,
):
    """dispatch_tool threads session_id/interaction_id into the StagedChange.

    A propose dispatch carrying ``session_id`` + ``interaction_id`` mints a token
    whose StagedChange records BOTH (so the resident's closure marker updates the
    right interaction and the session autonomy-grant scopes correctly). A dispatch
    WITHOUT the args still stages, leaving both ``None`` (back-compat for the
    external MCP / consent surfaces that never pass them).
    """
    from app.agentive.staging import get_token

    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()

    # With session_id + interaction_id -> both thread through to the StagedChange.
    r = await dispatch_tool(
        "integral_create_entry",
        {"track_id": track_id, "title": "Threaded", "text": "Q4 planning"},
        principal_id=auth_user_id,
        scope=workspace_id,
        session_id="sess-1",
        interaction_id="int-1",
    )
    _assert_staged(r, kind="create_entry")
    # On the wire the StagedChange carries the session scope key.
    assert r.data.get("session_id") == "sess-1", r.data
    sc = await get_token(r.data["token"])
    assert sc is not None
    assert sc.session_id == "sess-1", sc
    assert sc.interaction_id == "int-1", sc

    # Without the args -> still stages; both default to None (back-compat path
    # the external MCP + consent dispatch surfaces take).
    r2 = await dispatch_tool(
        "integral_create_entry",
        {"track_id": track_id, "title": "Unthreaded", "text": "Q4 planning"},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    _assert_staged(r2, kind="create_entry")
    assert r2.data.get("session_id") is None, r2.data
    sc2 = await get_token(r2.data["token"])
    assert sc2 is not None
    assert sc2.session_id is None, sc2
    assert sc2.interaction_id is None, sc2


@pytest.mark.asyncio
async def test_propose_no_stage_escape(
    bind_fresh_graph_context_for_async_tests,
):
    """A stager returning ``{"_no_stage": True, "data": ...}`` mints NO StagedChange.

    The no-stage escape lets a stager signal "don't stage — return this data
    directly" (a later task's ``file_content`` unresolved branch returns
    candidate matches rather than staging a write). ``_dispatch_propose`` returns
    a non-error ``ToolResult`` carrying the escape's ``data`` and never calls
    ``create_staged_change`` — so no token is minted.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from app.agentive.staging import get_pending_for_user
    from app.agentive.tooling.dispatch import _dispatch_propose

    auth_user_id, workspace_id, _track_id = await _bootstrap_principal_and_track()

    spec = SimpleNamespace(name="integral_synthetic_no_stage", op_class="propose")
    binding = SimpleNamespace(
        direct_ref=None,
        stager=lambda args: {"_no_stage": True, "data": {"candidates": [1, 2]}},
    )

    with patch(
        "app.agentive.staging.create_staged_change", new_callable=AsyncMock
    ) as mock_create:
        r = await _dispatch_propose(
            spec,
            binding,
            {},
            principal_id=auth_user_id,
            scope=workspace_id,
            session_id="sess-x",
            interaction_id="int-x",
        )

    assert not r.is_error, r
    assert r.data == {"candidates": [1, 2]}, r
    # The escape returns BEFORE minting — create_staged_change is never called.
    mock_create.assert_not_called()
    # And nothing is staged for the principal.
    pending = await get_pending_for_user(auth_user_id)
    assert pending == [], pending


# --------------------------------------------------------------------------- #
# Task 4 — update_track / delete_track / delete_entry / save_view proposes.
# Each proves: dispatch stages (token + kind) AND does NOT apply the mutation
# (the track/entry survives unchanged; no view is created). The executors
# already exist in staging_executors._EXECUTORS; this task wires the stagers +
# bindings + manifest so the propose path can mint a StagedChange.
# --------------------------------------------------------------------------- #
async def _track_title(auth_user_id, workspace_id, track_id) -> str:
    """Return ``track_id``'s title via the wired ``integral_list_tracks`` read."""
    r = await dispatch_tool(
        "integral_list_tracks", {}, principal_id=auth_user_id, scope=workspace_id
    )
    assert not r.is_error, r
    for t in r.data.get("tracks", []):
        if t.get("id") == track_id:
            return t.get("title")
    raise AssertionError(f"track {track_id} not found in list: {r.data}")


def _track_ids(list_data) -> list:
    return [t.get("id") for t in (list_data or {}).get("tracks", [])]


async def _list_tracks(auth_user_id, workspace_id):
    r = await dispatch_tool(
        "integral_list_tracks", {}, principal_id=auth_user_id, scope=workspace_id
    )
    assert not r.is_error, r
    return r.data


async def _count_views(auth_user_id, workspace_id, track_id) -> int:
    """Count views on ``track_id`` via the wired ``integral_list_views`` read."""
    r = await dispatch_tool(
        "integral_list_views",
        {"track_id": track_id},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert not r.is_error, r
    return len(r.data.get("views", []))


@pytest.mark.asyncio
async def test_update_track_propose_returns_staged_token_no_change(
    bind_fresh_graph_context_for_async_tests,
):
    """integral_update_track stages an update_track — and changes NO track.

    The manifest tool nests changes under ``updates``; the stager FLATTENS them
    into top-level payload keys (mirroring ``_stage_update_entry``) so the
    ``_x_update_track`` executor splats ``{track_id, **fields}``. The track's
    title is unchanged after the propose dispatch (the executor runs only on a
    later user bless).
    """
    from app.agentive.staging import get_token

    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()
    before_title = await _track_title(auth_user_id, workspace_id, track_id)
    assert before_title == "Seeded Track"

    r = await dispatch_tool(
        "integral_update_track",
        {"track_id": track_id, "updates": {"title": "New Title"}},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    _assert_staged(r, kind="update_track")
    dm = r.data["diff_machine"]
    assert dm.get("track_id") == track_id, dm
    assert dm.get("title") == "New Title", dm
    # The flattened field reaches the staged payload (what the executor splats).
    sc = await get_token(r.data["token"])
    assert sc is not None
    assert sc.payload.get("track_id") == track_id, sc.payload
    assert sc.payload.get("title") == "New Title", sc.payload

    after_title = await _track_title(auth_user_id, workspace_id, track_id)
    assert after_title == before_title, f"propose must not apply: {after_title!r}"


@pytest.mark.asyncio
async def test_update_track_no_changes_fails_closed(
    bind_fresh_graph_context_for_async_tests,
):
    """update_track with an empty ``updates`` fails closed — no token minted."""
    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()

    r = await dispatch_tool(
        "integral_update_track",
        {"track_id": track_id, "updates": {}},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert r.is_error, r


@pytest.mark.asyncio
async def test_delete_track_propose_returns_staged_token_track_survives(
    bind_fresh_graph_context_for_async_tests,
):
    """integral_delete_track stages a delete_track — and the track still exists."""
    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()
    before = _track_ids(await _list_tracks(auth_user_id, workspace_id))
    assert track_id in before

    r = await dispatch_tool(
        "integral_delete_track",
        {"track_id": track_id},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    _assert_staged(r, kind="delete_track")
    assert r.data["diff_machine"].get("track_id") == track_id, r.data

    after = _track_ids(await _list_tracks(auth_user_id, workspace_id))
    assert track_id in after, f"propose must not apply (track deleted): {after}"


@pytest.mark.asyncio
async def test_delete_entry_propose_returns_staged_token_entry_survives(
    bind_fresh_graph_context_for_async_tests,
):
    """integral_delete_entry stages a delete_entry — and the entry count holds.

    The seeded entry is still listed after the propose dispatch (only a later
    user bless runs ``_x_delete_entry``).
    """
    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()
    entry_id = await _seed_entry(auth_user_id, workspace_id, track_id)
    before = await _count_entries(auth_user_id, workspace_id, track_id)
    assert before == 1

    r = await dispatch_tool(
        "integral_delete_entry",
        {"entry_id": entry_id},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    _assert_staged(r, kind="delete_entry")
    assert r.data["diff_machine"].get("entry_id") == entry_id, r.data

    after = await _count_entries(auth_user_id, workspace_id, track_id)
    assert after == before, f"propose must not apply (entry deleted): {after}"


@pytest.mark.asyncio
async def test_save_view_propose_returns_staged_token_no_view(
    bind_fresh_graph_context_for_async_tests,
):
    """integral_save_view stages a save_view — and creates NO view.

    The track's view count is unchanged after the propose dispatch; the payload
    matches the ``_x_save_view`` service splat (``{track_id, name, view_type,
    config}``).
    """
    from app.agentive.staging import get_token

    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()
    before = await _count_views(auth_user_id, workspace_id, track_id)

    r = await dispatch_tool(
        "integral_save_view",
        {
            "track_id": track_id,
            "name": "My View",
            "view_type": "feed",
            "config": {},
        },
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    _assert_staged(r, kind="save_view")
    dm = r.data["diff_machine"]
    assert dm.get("track_id") == track_id, dm
    assert dm.get("name") == "My View", dm
    assert dm.get("view_type") == "feed", dm
    sc = await get_token(r.data["token"])
    assert sc is not None
    assert sc.payload.get("config") == {}, sc.payload

    after = await _count_views(auth_user_id, workspace_id, track_id)
    assert after == before, f"propose must not apply (view created): {after}"


@pytest.mark.asyncio
async def test_save_view_defaults_view_type_feed(
    bind_fresh_graph_context_for_async_tests,
):
    """save_view with no view_type defaults to ``feed`` (matches the executor)."""
    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()

    r = await dispatch_tool(
        "integral_save_view",
        {"track_id": track_id, "name": "Defaulted"},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    _assert_staged(r, kind="save_view")
    assert r.data["diff_machine"].get("view_type") == "feed", r.data


# --------------------------------------------------------------------------- #
# Task 5 — integral_file_content (fused classify→stage with the unresolved
# candidates branch). Confident classification stages a ``file_content`` change
# carrying a ``_personalization`` block; an ambiguous one returns NON-staged
# filing candidates via the ``_no_stage`` escape (``_kind: filing_candidates``).
# --------------------------------------------------------------------------- #
async def _bootstrap_principal_no_track():
    """Create an AuthUser + User + personal workspace with NO track in it.

    Returns ``(auth_user_id, workspace_id)``. Used by the unresolved-filing
    test: with zero accessible tracks and no hints, staging returns
    ``filing_status == "unresolved"``.
    """
    from jvspatial.api.auth.models import UserCreate

    from app.api.auth import _get_auth_service
    from app.models.nodes import User
    from app.services.app_graph import catalog_user
    from app.services.personal_workspace import ensure_personal_workspace

    auth_service = _get_auth_service()
    user_response = await auth_service.register_user(
        UserCreate(email="file-content-empty@example.com", password="testpassword123")
    )
    auth_user_id = user_response.id
    user_node = await User.create(user_id=auth_user_id, display_name="No Track User")
    await catalog_user(user_node)
    ws = await ensure_personal_workspace(user_node)
    return auth_user_id, (ws.id if ws else None)


@pytest.mark.asyncio
async def test_file_content_confident_stages(
    bind_fresh_graph_context_for_async_tests,
):
    """integral_file_content stages when track + type_hint are supplied."""
    from app.agentive.staging import get_token

    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()
    before = await _count_entries(auth_user_id, workspace_id, track_id)

    schema_r = await dispatch_tool(
        "integral_get_track_schema",
        {"track_id": track_id},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert not schema_r.is_error, schema_r
    entry_types = schema_r.data.get("entry_types") or []
    assert entry_types, "seeded track must expose entry types for this test"
    type_hint = entry_types[0].get("name") or entry_types[0].get("key")

    r = await dispatch_tool(
        "integral_file_content",
        {
            "text": "Q4 planning notes for the launch",
            "track_id": track_id,
            "type_hint": type_hint,
            "title": "Q4 planning notes",
        },
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    _assert_staged(r, kind="file_content")
    # Confident path → staged_change discriminator (NOT filing_candidates).
    assert r.data.get("_kind") == "staged_change", r.data
    dm = r.data["diff_machine"]
    assert dm.get("track_id") == track_id, dm
    # The personalization block is always emitted (here with no learned bias).
    assert "_personalization" in dm, dm
    assert dm["_personalization"].get("used_learned_track") is False, dm
    assert dm["_personalization"].get("used_learned_type") is False, dm

    # Staged, not applied: the executor's payload shape is present and matches
    # what _x_file_content splats (track_id + title), and no entry was created.
    sc = await get_token(r.data["token"])
    assert sc is not None
    assert sc.payload.get("track_id") == track_id, sc.payload
    assert sc.payload.get("title"), sc.payload

    after = await _count_entries(auth_user_id, workspace_id, track_id)
    assert after == before, f"propose must not apply: {before} -> {after}"


@pytest.mark.asyncio
async def test_file_content_unresolved_no_stage(
    bind_fresh_graph_context_for_async_tests,
):
    """Missing track/type hints return NON-staged candidates — mints no token."""
    from app.agentive.staging import get_pending_for_user

    auth_user_id, workspace_id = await _bootstrap_principal_no_track()

    r = await dispatch_tool(
        "integral_file_content",
        {"text": "something ambiguous with no home"},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert not r.is_error, r
    assert r.data.get("_kind") == "filing_candidates", r.data
    assert r.data.get("filing_status") == "unresolved", r.data
    # No StagedChange was minted — the no-stage escape never touches the store.
    pending = await get_pending_for_user(auth_user_id)
    assert pending == [], pending


@pytest.mark.asyncio
async def test_file_content_missing_text_no_stage(
    bind_fresh_graph_context_for_async_tests,
):
    """file_content with no text returns a filing_candidates error — no token."""
    from app.agentive.staging import get_pending_for_user

    auth_user_id, workspace_id, _track_id = await _bootstrap_principal_and_track()

    r = await dispatch_tool(
        "integral_file_content",
        {},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert not r.is_error, r
    assert r.data.get("_kind") == "filing_candidates", r.data
    assert r.data.get("error") is True, r.data
    pending = await get_pending_for_user(auth_user_id)
    assert pending == [], pending


@pytest.mark.asyncio
async def test_file_content_missing_required_field_asks_not_stages(
    bind_fresh_graph_context_for_async_tests,
):
    """A required field with no default and no supplied value blocks staging.

    Regression for a live bug: staging went ahead, the user approved the
    card, and ONLY THEN did entry creation fail server-side with an opaque
    "Field 'frequency' is required" — a wasted round trip the create/
    drop-field retry in staging_executors.py can never recover from (you
    cannot fix "missing" by dropping the field that's missing). This proves
    the gap is caught before a card is even shown, with a message that
    steers the agent to integral_ask_user instead of guessing.
    """
    from app.agentive.staging import get_pending_for_user
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.entry_types import create_entry_type

    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()

    created_et = await invoke_route_in_process(
        create_entry_type,
        principal_id=auth_user_id,
        scope=workspace_id,
        track_id=track_id,
        name="Pay Run",
        form_schema={
            "fields": [
                {
                    "key": "frequency",
                    "name": "Frequency",
                    "type": "select",
                    "required": True,
                    "enum": ["monthly", "biweekly", "weekly"],
                    # No `default` — the case that can never be satisfied by
                    # content_profile_entry_fields.py's own default fallback.
                },
            ],
        },
    )
    assert not (isinstance(created_et, dict) and created_et.get("error")), created_et

    r = await dispatch_tool(
        "integral_file_content",
        {
            "text": "December 2026 pay run",
            "track_id": track_id,
            "type_hint": "Pay Run",
            "title": "December 2026",
        },
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert not r.is_error, r
    assert r.data.get("_kind") == "filing_candidates", r.data
    assert "frequency" in (r.data.get("message") or "").lower(), r.data
    assert "integral_ask_user" in (r.data.get("message") or ""), r.data
    pending = await get_pending_for_user(auth_user_id)
    assert pending == [], "must not stage — nothing for the user to approve yet"


@pytest.mark.asyncio
async def test_file_content_text_nested_in_fields_recovers(
    bind_fresh_graph_context_for_async_tests,
):
    """A model nesting the body under fields.text (not top-level text) still stages.

    Common agent mistake: schema-driven `fields` reads like the right home for
    every value, including the free-text body. The stager recovers it from
    `fields.text` instead of failing the whole card, and does not forward a
    stray `text` key into entry-type field normalization.
    """
    from app.agentive.staging import get_token

    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()
    schema_r = await dispatch_tool(
        "integral_get_track_schema",
        {"track_id": track_id},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert not schema_r.is_error, schema_r
    entry_types = schema_r.data.get("entry_types") or []
    assert entry_types, "seeded track must expose entry types for this test"
    type_hint = entry_types[0].get("name") or entry_types[0].get("key")

    r = await dispatch_tool(
        "integral_file_content",
        {
            "track_id": track_id,
            "type_hint": type_hint,
            "title": "Nested text",
            "fields": {"text": "Body nested under fields instead of top-level"},
        },
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    _assert_staged(r, kind="file_content")
    assert r.data.get("_kind") == "staged_change", r.data
    sc = await get_token(r.data["token"])
    assert sc is not None
    assert sc.payload.get("body") == "Body nested under fields instead of top-level"
    # The recovered key must not also survive as a custom field.
    assert "text" not in (sc.payload.get("fields") or {}), sc.payload


@pytest.mark.asyncio
async def test_file_content_identity_from_principal_not_args(
    bind_fresh_graph_context_for_async_tests,
):
    """A forged ``user_id`` arg never affects the principal or the staged change.

    The stager reads the acting principal via ``_bound_propose_principal()`` (the
    dispatch principal), NEVER the args (PC-1). A smuggled ``user_id`` is ignored:
    the minted StagedChange's user_id is the dispatch principal and no ``user_id``
    leaks into the executor payload.
    """
    from app.agentive.staging import get_token

    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()
    schema_r = await dispatch_tool(
        "integral_get_track_schema",
        {"track_id": track_id},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert not schema_r.is_error, schema_r
    entry_types = schema_r.data.get("entry_types") or []
    assert entry_types, "seeded track must expose entry types for this test"
    type_hint = entry_types[0].get("name") or entry_types[0].get("key")

    r = await dispatch_tool(
        "integral_file_content",
        {
            "user_id": "ATTACKER",
            "text": "Forged-identity filing attempt",
            "focused_track_id": track_id,
            "type_hint": type_hint,
        },
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    _assert_staged(r, kind="file_content")
    sc = await get_token(r.data["token"])
    assert sc is not None
    assert sc.user_id == auth_user_id  # identity from dispatch, not args
    assert "user_id" not in sc.payload  # stager mapped data only


@pytest.mark.asyncio
async def test_file_content_in_catalogue(
    bind_fresh_graph_context_for_async_tests,
):
    """integral_file_content is advertised (existing + dispatchable) and validates."""
    from app.agentive.tooling.catalogue import build_tool_catalogue
    from app.agentive.tooling.manifest import load_manifest, validate_manifest

    reg = load_manifest()
    validate_manifest({n: t for n, t in reg.items() if t.status == "existing"})

    spec = reg.get("integral_file_content")
    assert spec is not None
    assert spec.op_class == "propose"
    assert spec.staging_kind == "file_content"

    names = {entry["name"] for entry in build_tool_catalogue()}
    assert "integral_file_content" in names


# ---------------------------------------------------------------------------
# B-AGENT-03 — duplicate "Contacts" tracks across workspaces (propose path)
# ---------------------------------------------------------------------------


async def _bootstrap_dual_contacts_tracks():
    """Personal workspace + second org workspace, each with a Contacts track."""
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.tracks import create_track, update_track
    from app.api.workspaces import create_workspace

    auth_user_id, personal_ws, personal_track = await _bootstrap_principal_and_track()

    renamed = await invoke_route_in_process(
        update_track,
        principal_id=auth_user_id,
        scope=personal_ws,
        track_id=personal_track,
        title="Contacts",
    )
    assert not (isinstance(renamed, dict) and renamed.get("error")), renamed

    ws_b_result = await invoke_route_in_process(
        create_workspace,
        principal_id=auth_user_id,
        scope=personal_ws,
        name="Contacts Dup Org",
    )
    assert not (isinstance(ws_b_result, dict) and ws_b_result.get("error")), ws_b_result
    org_ws = ws_b_result["workspace"]["id"]

    org_track_result = await invoke_route_in_process(
        create_track,
        principal_id=auth_user_id,
        scope=org_ws,
        title="Contacts",
        visibility="private",
        workspace_id=org_ws,
    )
    assert not (
        isinstance(org_track_result, dict) and org_track_result.get("error")
    ), org_track_result
    org_track_id = org_track_result["track"]["id"]
    return auth_user_id, personal_ws, personal_track, org_ws, org_track_id


@pytest.mark.asyncio
async def test_create_entry_track_hint_stages_active_workspace_contacts(
    bind_fresh_graph_context_for_async_tests,
):
    """track_hint Contacts + scope=W2 stages W2's Contacts track, not W1's."""
    from app.agentive.staging import get_token

    auth_user_id, _personal_ws, personal_track, org_ws, org_track_id = (
        await _bootstrap_dual_contacts_tracks()
    )

    r = await dispatch_tool(
        "integral_create_entry",
        {"text": "Pat Smith pat@example.com", "track_hint": "Contacts"},
        principal_id=auth_user_id,
        scope=org_ws,
    )
    _assert_staged(r, kind="create_entry")
    sc = await get_token(r.data["token"])
    assert sc is not None
    assert sc.payload.get("track_id") == org_track_id
    assert sc.payload.get("track_id") != personal_track


@pytest.mark.asyncio
async def test_file_content_track_hint_stages_active_workspace_contacts(
    bind_fresh_graph_context_for_async_tests,
):
    """integral_file_content with track_hint Contacts honors dispatch scope."""
    from app.agentive.staging import get_token

    auth_user_id, _personal_ws, personal_track, org_ws, org_track_id = (
        await _bootstrap_dual_contacts_tracks()
    )
    schema_r = await dispatch_tool(
        "integral_get_track_schema",
        {"track_id": org_track_id},
        principal_id=auth_user_id,
        scope=org_ws,
    )
    assert not schema_r.is_error, schema_r
    entry_types = schema_r.data.get("entry_types") or []
    assert entry_types, "Contacts track must expose entry types for this test"
    type_hint = entry_types[0].get("name") or entry_types[0].get("key")

    r = await dispatch_tool(
        "integral_file_content",
        {
            "text": "Jamie Lee jamie@example.com",
            "track_hint": "Contacts",
            "type_hint": type_hint,
        },
        principal_id=auth_user_id,
        scope=org_ws,
    )
    _assert_staged(r, kind="file_content")
    sc = await get_token(r.data["token"])
    assert sc is not None
    assert sc.payload.get("track_id") == org_track_id
    assert sc.payload.get("track_id") != personal_track
