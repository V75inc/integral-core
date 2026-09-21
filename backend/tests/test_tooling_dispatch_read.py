"""dispatch_tool read path: route-backed read tools dispatch; unknown rejected; errors enveloped.

These exercise the Task-3 dispatch mechanism end-to-end: ``dispatch_tool`` looks
up the manifest ``ToolSpec`` + the ``ToolBinding``, lazily imports the backing
route handler, and calls it via ``invoke_route_in_process`` under a resolved
principal + bound scope. The inline bootstrap mirrors
``tests/test_tooling_invoke.py`` (no HTTP signup; real route handlers seed the
graph) so the tools read exactly the code path the substrate exposes.
"""

import pytest

from app.agentive.tooling.dispatch import ToolResult, dispatch_tool


async def _bootstrap_principal_and_track():
    """Create an AuthUser + User + personal workspace, then a track in it.

    Returns ``(auth_user_id, workspace_id)``. Mirrors
    ``test_tooling_invoke._bootstrap_principal_and_track``.
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
        UserCreate(email="dispatch-test@example.com", password="testpassword123")
    )
    auth_user_id = user_response.id
    user_node = await User.create(user_id=auth_user_id, display_name="Dispatch Test")
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


async def _seed_app(auth_user_id, workspace_id):
    """Seed an App in ``workspace_id`` via the real create_app handler."""
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.apps import create_app

    created = await invoke_route_in_process(
        create_app,
        principal_id=auth_user_id,
        scope=workspace_id,
        name="Seeded App",
        workspace_id=workspace_id,
    )
    assert not (isinstance(created, dict) and created.get("error")), created
    return created["app"]["id"]


@pytest.mark.asyncio
async def test_dispatch_unknown_tool_rejected():
    """An unknown tool name yields a clean error envelope, not an exception."""
    r = await dispatch_tool("integral_not_a_tool", {}, principal_id="u", scope=None)
    assert isinstance(r, ToolResult)
    assert r.is_error and "unknown" in r.error_code


@pytest.mark.asyncio
async def test_dispatch_list_tracks_read(bind_fresh_graph_context_for_async_tests):
    """A route-backed read tool dispatches and returns non-error data."""
    auth_user_id, workspace_id, _track_id = await _bootstrap_principal_and_track()

    r = await dispatch_tool(
        "integral_list_tracks", {}, principal_id=auth_user_id, scope=workspace_id
    )
    assert not r.is_error, r
    assert r.data is not None
    titles = [t.get("title") for t in r.data.get("tracks", [])]
    assert "Seeded Track" in titles
    assert r.data.get("has_more") is False


@pytest.mark.asyncio
async def test_dispatch_list_tracks_auto_paginates(
    bind_fresh_graph_context_for_async_tests,
):
    """Agent list_tracks expands pages when cursor/limit are omitted."""
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.tracks import create_track

    auth_user_id, workspace_id, _track_id = await _bootstrap_principal_and_track()
    for i in range(24):
        await invoke_route_in_process(
            create_track,
            principal_id=auth_user_id,
            scope=workspace_id,
            title=f"Bulk Track {i:02d}",
            visibility="private",
            workspace_id=workspace_id,
        )

    r = await dispatch_tool(
        "integral_list_tracks", {}, principal_id=auth_user_id, scope=workspace_id
    )
    assert not r.is_error, r
    tracks = r.data.get("tracks") or []
    assert len(tracks) >= 25
    assert r.data.get("has_more") is False
    assert r.data.get("total") == len(tracks)


@pytest.mark.asyncio
async def test_dispatch_resolve_entry_path_param(
    bind_fresh_graph_context_for_async_tests,
):
    """A read tool with a path param maps args -> handler kwargs and resolves."""
    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()
    entry_id = await _seed_entry(auth_user_id, workspace_id, track_id)

    r = await dispatch_tool(
        "integral_resolve_entry",
        {"entry_id": entry_id},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert not r.is_error, r
    assert r.data is not None
    assert r.data["entry"]["id"] == entry_id


@pytest.mark.asyncio
async def test_dispatch_resolve_entry_missing_entry_id_is_clean_error(
    bind_fresh_graph_context_for_async_tests,
):
    """A required path param the model forgot to supply must come back as a
    clean, actionable ``missing_argument`` ToolResult — never a raw
    ``TypeError`` from the handler (real incident: ``integral_resolve_entry``
    called with no ``entry_id`` crashed as ``get_entry() missing 1 required
    positional argument: 'entry_id'``, surfaced to the model only as an
    opaque ``internal_error`` it had no way to self-correct on)."""
    auth_user_id, workspace_id, _track_id = await _bootstrap_principal_and_track()

    r = await dispatch_tool(
        "integral_resolve_entry",
        {},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert r.is_error
    assert r.error_code == "missing_argument"
    assert "entry_id" in r.message


@pytest.mark.asyncio
async def test_dispatch_query_post_body(bind_fresh_graph_context_for_async_tests):
    """A POST-body read (integral_query) threads tool args into request.json().

    The ``/api/retrieve`` handler reads its body via ``await request.json()`` and
    validates it via ``RetrieveRequest.model_validate``. The Part-A ``body_map``
    passes the tool args through as the JSON body. ``mode=graph`` keeps the call
    off the (unavailable in test) vector store; the graph channel is already
    permission-gated so the dispatch returns a non-error ToolResult.
    """
    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()
    await _seed_entry(auth_user_id, workspace_id, track_id)

    r = await dispatch_tool(
        "integral_query",
        {"query": "Seeded", "mode": "graph", "scope": f"track:{track_id}"},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert not r.is_error, r
    assert r.data is not None
    # RetrieveResponse shape: results + mode echo.
    assert "results" in r.data
    assert r.data.get("mode") == "graph"


@pytest.mark.asyncio
async def test_dispatch_describe_operational_model_service(
    bind_fresh_graph_context_for_async_tests,
):
    """A SERVICE-backed read (integral_describe_model) dispatches end-to-end.

    Proves the Part-B service-binding path: ``service_ref`` resolves the
    ``operational_model_authoring.describe_operational_model`` fn, the dispatch injects ``user_id =
    principal_id`` (never from args), binds the scope ContextVar, and returns a
    non-error ToolResult carrying the published-profile payload for the track
    seeded under the bootstrapped principal+scope.
    """
    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()

    r = await dispatch_tool(
        "integral_describe_model",
        {"track_id": track_id},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert not r.is_error, r
    assert r.data is not None
    # describe_operational_model returns {published, draft, has_draft} for a track CP.
    assert "published" in r.data
    assert "has_draft" in r.data


@pytest.mark.asyncio
async def test_service_read_resets_scope_contextvar(
    bind_fresh_graph_context_for_async_tests,
):
    """The scope ContextVar is reset after a service dispatch (no cross-call leak).

    The dispatcher binds ``current_scope_workspace_id`` to the dispatch scope
    around the service call and resets it in ``finally``. After dispatch the
    ContextVar must read its prior value (``None`` here), proving a service call
    can't leak its workspace scope into a later, differently-scoped call (PC-2).
    """
    from app.services.agent_scope import current_scope_workspace_id

    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()

    assert current_scope_workspace_id.get() is None
    r = await dispatch_tool(
        "integral_describe_model",
        {"track_id": track_id},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert not r.is_error, r
    # The binding was reset; the dispatcher did not leak ``workspace_id``.
    assert current_scope_workspace_id.get() is None


@pytest.mark.asyncio
async def test_dispatch_count_entries(bind_fresh_graph_context_for_async_tests):
    """``integral_count_entries`` (SERVICE agent_insights.count_entries_grouped).

    Seeds an entry, then counts grouped by track under the bootstrapped
    principal + bound scope. Asserts the count envelope shape (``groups`` +
    ``total_matched``) comes back non-error, and that the seeded entry is
    reflected in the total (> 0).
    """
    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()
    await _seed_entry(auth_user_id, workspace_id, track_id)

    r = await dispatch_tool(
        "integral_count_entries",
        {"group_by": "track"},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert not r.is_error, r
    assert r.data is not None
    assert "groups" in r.data
    assert "total_matched" in r.data
    assert r.data["total_matched"] >= 1
    assert any(g.get("key") == track_id for g in r.data["groups"]), r.data


@pytest.mark.asyncio
async def test_dispatch_activity_digest(bind_fresh_graph_context_for_async_tests):
    """``integral_activity_digest`` (SERVICE agent_insights.activity_digest).

    Seeds an entry, then asks for a user-scoped digest of today's activity.
    Asserts the digest envelope shape (``track_summaries`` + ``total_entries``)
    and that the bound dispatch scope was passed through as ``workspace_id`` —
    the proof that the dispatcher scopes the SERVICE read to the active
    workspace (see ``_dispatch_service_read``'s workspace_id injection).
    """
    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()
    await _seed_entry(auth_user_id, workspace_id, track_id)

    r = await dispatch_tool(
        "integral_activity_digest",
        {"scope": "user", "period": "today"},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert not r.is_error, r
    assert r.data is not None
    assert "track_summaries" in r.data
    assert "total_entries" in r.data
    assert r.data["total_entries"] >= 1
    # The dispatcher injected the bound scope as workspace_id (PC-2): the digest
    # echoes it back, proving the service ran workspace-scoped (not cross-ws).
    assert r.data.get("workspace_id") == workspace_id, r.data


@pytest.mark.asyncio
async def test_dispatch_insights_scope_isolation(
    bind_fresh_graph_context_for_async_tests,
):
    """Count + digest are workspace-scoped: a WS_A dispatch excludes WS_B entries.

    Mirrors ``test_tooling_invoke.test_invoke_scope_isolation``: the same user
    owns a second workspace (WS_B) holding its own track + entry. Bound to
    WS_A, neither the count nor the digest may surface WS_B's track/entry — the
    dispatcher passes the bound scope as ``workspace_id`` to the service, which
    applies the B-AGENT-03 workspace gate. If the scope were not bound, the
    services would fall back to cross-workspace behaviour and leak WS_B's
    records — exactly what this gates against.
    """
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.entries import create_entry
    from app.api.tracks import create_track
    from app.api.workspaces import create_workspace

    auth_user_id, ws_a, track_a = await _bootstrap_principal_and_track()
    await _seed_entry(auth_user_id, ws_a, track_a)

    # WS_B: a second workspace the same user owns, with its own track + entry.
    ws_b_result = await invoke_route_in_process(
        create_workspace,
        principal_id=auth_user_id,
        scope=ws_a,
        name="Insights Scope WS_B",
    )
    assert not (isinstance(ws_b_result, dict) and ws_b_result.get("error")), ws_b_result
    ws_b = ws_b_result["workspace"]["id"]
    assert ws_b != ws_a

    track_b_result = await invoke_route_in_process(
        create_track,
        principal_id=auth_user_id,
        scope=ws_b,
        title="Track B (WS_B only)",
        visibility="private",
        workspace_id=ws_b,
    )
    assert not (
        isinstance(track_b_result, dict) and track_b_result.get("error")
    ), track_b_result
    track_b = track_b_result["track"]["id"]
    entry_b = await invoke_route_in_process(
        create_entry,
        principal_id=auth_user_id,
        scope=ws_b,
        track_id=track_b,
        title="Entry B (WS_B only)",
    )
    assert not (isinstance(entry_b, dict) and entry_b.get("error")), entry_b

    # Count bound to WS_A: WS_B's track must NOT appear in the groups.
    count_a = await dispatch_tool(
        "integral_count_entries",
        {"group_by": "track"},
        principal_id=auth_user_id,
        scope=ws_a,
    )
    assert not count_a.is_error, count_a
    count_a_keys = {g.get("key") for g in count_a.data.get("groups", [])}
    assert track_b not in count_a_keys, count_a.data
    assert track_a in count_a_keys, count_a.data

    # Digest bound to WS_A: WS_B's track must NOT appear; workspace_id echoes WS_A.
    digest_a = await dispatch_tool(
        "integral_activity_digest",
        {"scope": "user", "period": "today"},
        principal_id=auth_user_id,
        scope=ws_a,
    )
    assert not digest_a.is_error, digest_a
    assert digest_a.data.get("workspace_id") == ws_a, digest_a.data
    digest_a_track_ids = {
        ts.get("track_id") for ts in digest_a.data.get("track_summaries", [])
    }
    assert track_b not in digest_a_track_ids, digest_a.data
    assert track_a in digest_a_track_ids, digest_a.data

    # Sanity: bound to WS_B, WS_B's track now DOES appear (the only lever is scope).
    digest_b = await dispatch_tool(
        "integral_activity_digest",
        {"scope": "user", "period": "today"},
        principal_id=auth_user_id,
        scope=ws_b,
    )
    assert not digest_b.is_error, digest_b
    digest_b_track_ids = {
        ts.get("track_id") for ts in digest_b.data.get("track_summaries", [])
    }
    assert track_b in digest_b_track_ids, digest_b.data
    assert track_a not in digest_b_track_ids, digest_b.data


@pytest.mark.asyncio
async def test_dispatch_whoami(bind_fresh_graph_context_for_async_tests):
    """``integral_whoami`` (GET /api/auth/me) returns the acting user's profile.

    Takes NO args — identity is the bound dispatch principal (PC-1), so the
    binding has no param_map. The ``get_current_user`` handler returns
    ``{"user": {...}, "message": str}``; assert the user envelope comes back for
    the bootstrapped principal.
    """
    auth_user_id, workspace_id, _track_id = await _bootstrap_principal_and_track()

    r = await dispatch_tool(
        "integral_whoami", {}, principal_id=auth_user_id, scope=workspace_id
    )
    assert not r.is_error, r
    assert r.data is not None
    assert "user" in r.data
    assert r.data["user"]


@pytest.mark.asyncio
async def test_dispatch_get_app(bind_fresh_graph_context_for_async_tests):
    """``integral_get_app`` (GET /api/apps/{app_id}) resolves a seeded app.

    Maps the ``app_id`` arg -> the handler path kwarg (``_pick("app_id")``); the
    ``get_app`` handler is policy-gated (``app.read``) and returns
    ``{"app": {...}}`` for an app the bootstrapped owner can read.
    """
    auth_user_id, workspace_id, _track_id = await _bootstrap_principal_and_track()
    app_id = await _seed_app(auth_user_id, workspace_id)

    r = await dispatch_tool(
        "integral_get_app",
        {"app_id": app_id},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert not r.is_error, r
    assert r.data is not None
    assert r.data["app"]["id"] == app_id


@pytest.mark.asyncio
async def test_list_apps_now_existing(bind_fresh_graph_context_for_async_tests):
    """``integral_list_apps`` is advertised (status existing) and dispatches.

    Its binding already existed; promoting its manifest status to ``existing``
    makes it appear in the catalogue and dispatchable. Seed an app, then list.
    """
    from app.agentive.tooling.catalogue import build_tool_catalogue

    auth_user_id, workspace_id, _track_id = await _bootstrap_principal_and_track()
    app_id = await _seed_app(auth_user_id, workspace_id)

    names = {entry["name"] for entry in build_tool_catalogue()}
    assert "integral_list_apps" in names

    r = await dispatch_tool(
        "integral_list_apps", {}, principal_id=auth_user_id, scope=workspace_id
    )
    assert not r.is_error, r
    assert r.data is not None
    app_ids = [a.get("id") for a in r.data.get("apps", [])]
    assert app_id in app_ids


async def _seed_attachment(
    entry_id, *, text="", scan_status=None, metadata_status="complete"
):
    """Attach an Attachment node to ``entry_id`` (no HTTP multipart in tests)."""
    from app.models.edges import HAS_ATTACHMENT
    from app.models.nodes import Attachment, Entry

    entry = await Entry.get(entry_id)
    kwargs = {
        "filename": "spec.pdf",
        "mime_type": "application/pdf",
        "size": 1024,
        "source_type": "file",
        "storage_key": "k",
        "metadata_status": metadata_status,
        "extracted_text": text,
    }
    if scan_status is not None:
        kwargs["scan_status"] = scan_status
    att = await Attachment.create(**kwargs)
    await entry.connect(att, edge=HAS_ATTACHMENT)
    return att


@pytest.mark.asyncio
async def test_dispatch_list_attachments_read(bind_fresh_graph_context_for_async_tests):
    """integral_list_attachments dispatches via service_ref; omits the text body."""
    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()
    entry_id = await _seed_entry(auth_user_id, workspace_id, track_id)
    await _seed_attachment(entry_id, text="z" * 300)

    r = await dispatch_tool(
        "integral_list_attachments",
        {"entry_id": entry_id},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert not r.is_error, r
    assert r.data.get("_kind") == "attachment_list"
    assert r.data.get("total") == 1
    item = r.data["attachments"][0]
    assert "extracted_text" not in item  # body never shipped in the list
    assert item["has_text"] is True
    assert item["text_length"] == 300


@pytest.mark.asyncio
async def test_dispatch_get_attachment_text_read(
    bind_fresh_graph_context_for_async_tests,
):
    """integral_get_attachment_text dispatches and flags content untrusted."""
    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()
    entry_id = await _seed_entry(auth_user_id, workspace_id, track_id)
    att = await _seed_attachment(entry_id, text="y" * 100)

    r = await dispatch_tool(
        "integral_get_attachment_text",
        {"attachment_id": att.id},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert not r.is_error, r
    assert r.data["content_untrusted"] is True
    assert r.data["char_count"] == 100
    assert r.data["truncated"] is False
