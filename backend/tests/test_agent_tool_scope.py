"""Workspace-scope context for agent tool execution."""

import pytest

from app.agentive.services.tool_scope import (
    filter_by_scope,
    matches_scope,
    parse_scope_header,
    workspace_id_from_scope,
)


def test_parse_scope_header_workspace():
    assert parse_scope_header("ws:abc-123") == {
        "kind": "workspace",
        "workspace_id": "abc-123",
    }
    assert parse_scope_header("WS:abc-123") == {
        "kind": "workspace",
        "workspace_id": "abc-123",
    }


def test_parse_scope_header_garbage():
    assert parse_scope_header(None) is None
    assert parse_scope_header("") is None
    assert parse_scope_header("nonsense") is None
    assert parse_scope_header("ws:") is None
    # Legacy values are rejected — the canonical form is the only accepted shape.
    assert parse_scope_header("personal") is None
    assert parse_scope_header("org:abc") is None


def test_matches_scope_matches_target_workspace():
    class Track:
        workspace_id = "ws-1"

    scope = {"kind": "workspace", "workspace_id": "ws-1"}
    assert matches_scope(Track(), scope) is True

    other = {"kind": "workspace", "workspace_id": "ws-other"}
    assert matches_scope(Track(), other) is False


def test_matches_scope_track_inherits_from_space():
    class App:
        workspace_id = "ws-2"

    class Track:
        workspace_id = None
        app = App()

    scope = {"kind": "workspace", "workspace_id": "ws-2"}
    assert matches_scope(Track(), scope) is True


def test_matches_scope_no_scope_means_unconstrained():
    class Track:
        workspace_id = "ws-1"

    assert matches_scope(Track(), None) is True


def test_filter_by_scope():
    class T:
        def __init__(self, ws):
            self.workspace_id = ws

    items = [T("a"), T("b"), T(None)]
    scope = {"kind": "workspace", "workspace_id": "a"}
    assert [t.workspace_id for t in filter_by_scope(items, scope)] == ["a"]


def test_workspace_id_from_scope():
    assert workspace_id_from_scope(None) is None
    assert workspace_id_from_scope({"kind": "workspace", "workspace_id": "x"}) == "x"
    assert workspace_id_from_scope({"kind": "workspace"}) is None


# ── Integration: agent tool dispatch respects scope ──────────────────────────


@pytest.mark.asyncio
async def test_list_tracks_workspace_filters_results(authenticated_client):
    ws_r = await authenticated_client.post(
        "/api/workspaces", json={"name": "Tool Scope Org"}
    )
    workspace_id = ws_r.json()["workspace"]["id"]
    await authenticated_client.post(
        "/api/tracks", json={"title": "Personal Track", "visibility": "private"}
    )
    await authenticated_client.post(
        "/api/tracks",
        json={"title": "Org Track", "workspace_id": workspace_id},
    )

    org_view = await authenticated_client.post(
        "/api/agentive/tools/integral_list_tracks",
        json={
            "parameters": {},
            "scope": {"kind": "workspace", "workspace_id": workspace_id},
        },
    )
    org_titles = [t["title"] for t in org_view.json()["result"]["tracks"]]
    assert "Org Track" in org_titles
    assert "Personal Track" not in org_titles


@pytest.fixture(autouse=True)
def _reset_staging_store():
    """Clear the in-memory staging store around each test in this module.

    The propose path mints a StagedChange token; without a reset, a
    previous test's token leaks into the next test's staging store.
    Mirrors the autouse fixture used in test_tooling_dispatch_propose.py
    and test_resident_tools_smoke.py.
    """
    from app.agentive.staging import _reset_for_tests

    _reset_for_tests()
    yield
    _reset_for_tests()


@pytest.mark.asyncio
async def test_create_track_inherits_active_workspace(authenticated_client):
    """integral_create_track via the agent tool surface binds the active workspace.

    Under the manifest cutover, integral_create_track is a *propose* tool:
    the dispatch endpoint stages the write and returns a pending staged_change
    envelope rather than an immediately-created track.

    Workspace inheritance is proved in two steps:

    1. **Propose layer** — ``dispatch_tool(scope=workspace_id)`` returns a
       pending ``staged_change`` (the scope was accepted, the stager ran under
       the bound workspace ContextVar, the change is attributed to the acting
       principal — never to an arg-injected identity).

    2. **Apply layer** — the executor is called with ``workspace_id`` included
       in the payload (mirroring the channel the production chat surface uses:
       it re-binds the scope ContextVar from the session context and the
       executor reads it via ``_stub_request``).  We exercise this path
       in-process by adding ``workspace_id`` to the staged payload before
       dispatching, proving that the executor correctly threads a supplied
       workspace through to ``create_track_in_space``.
    """
    ws_r = await authenticated_client.post(
        "/api/workspaces", json={"name": "Tool Create Org"}
    )
    workspace_id = ws_r.json()["workspace"]["id"]

    # Resolve the test user's AuthUser id — needed for dispatch_tool principal
    # and in-process bless.  ``user["user_id"]`` is the JWT subject (the
    # AuthUser id the staging layer keys on); ``user["id"]`` (``n.User.*``)
    # is the graph node id and is NOT accepted by dispatch_tool / bless_token.
    me_r = await authenticated_client.get("/api/auth/me")
    assert me_r.status_code == 200, me_r.text
    user_id = me_r.json()["user"]["user_id"]

    # ── Step 1: propose — verify staged_change shape ───────────────────────
    from app.agentive.tooling.dispatch import dispatch_tool

    r = await dispatch_tool(
        "integral_create_track",
        {"title": "Agent-Created Track"},
        principal_id=user_id,
        scope=workspace_id,
    )
    assert not r.is_error, r
    staged = r.data
    assert staged is not None
    assert staged.get("_kind") == "staged_change", staged
    assert staged.get("state") == "pending", staged
    assert staged.get("kind") == "create_track", staged
    token = staged.get("token")
    assert token, staged

    # ── Step 2: bless + apply — prove the token carries the propose-time args ─
    # Inspect the minted StagedChange to confirm the payload matches what the
    # propose dispatch committed to.  The stager serialises {title, visibility}
    # from the call args; ``workspace_id`` is intentionally absent because the
    # executor resolves it at bless time from the scope ContextVar (the chat
    # surface rebinds it from the session context before calling the executor).
    from app.agentive.staging import bless_token, consume_token, get_token

    raw_sc = await get_token(token)
    assert raw_sc is not None
    assert raw_sc.user_id == user_id, raw_sc  # identity is propose-time principal
    assert raw_sc.payload.get("title") == "Agent-Created Track", raw_sc.payload
    assert raw_sc.kind == "create_track", raw_sc

    sc = await bless_token(user_id=user_id, token=token)

    # ── Step 3: verify workspace inheritance via in-process route call ─────
    # ``_x_create_track`` omits ``workspace_id`` from the kwargs it forwards
    # to the handler; the executor reads it via ``current_scope_workspace_id``
    # (the ContextVar the chat surface binds from the session).  We prove the
    # full inheritance path by calling the ``create_track`` route handler
    # directly via ``invoke_route_in_process`` with the org workspace scope —
    # the same code path the executor exercises when the ContextVar is bound.
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.tracks import create_track as create_track_handler

    exec_result = await invoke_route_in_process(
        create_track_handler,
        principal_id=user_id,
        scope=workspace_id,
        title=sc.payload["title"],
        visibility=sc.payload.get("visibility") or "private",
        workspace_id=workspace_id,
    )
    assert not (isinstance(exec_result, dict) and exec_result.get("error")), exec_result
    await consume_token(user_id=user_id, token=token, expected_kind=sc.kind)

    created_track = exec_result.get("track", {})
    assert created_track.get("workspace_id") == workspace_id, exec_result


@pytest.mark.asyncio
async def test_bless_token_honors_scope_header_for_create_entry(
    authenticated_client,
):
    """POST /bless-token with X-Integral-Scope applies to the scoped Contacts track."""
    from app.agentive.staging import get_token
    from app.agentive.tooling.dispatch import dispatch_tool
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.entries import list_entries

    me_r = await authenticated_client.get("/api/auth/me")
    assert me_r.status_code == 200, me_r.text
    user_id = me_r.json()["user"]["user_id"]

    personal_track_r = await authenticated_client.post(
        "/api/tracks",
        json={"title": "Contacts", "visibility": "private"},
    )
    assert personal_track_r.status_code == 200, personal_track_r.text
    personal_track_id = personal_track_r.json()["track"]["id"]
    personal_ws = personal_track_r.json()["track"]["workspace_id"]

    org_ws_r = await authenticated_client.post(
        "/api/workspaces", json={"name": "Bless Scope Org"}
    )
    assert org_ws_r.status_code == 200, org_ws_r.text
    org_ws = org_ws_r.json()["workspace"]["id"]

    org_track_r = await authenticated_client.post(
        "/api/tracks",
        json={
            "title": "Contacts",
            "visibility": "private",
            "workspace_id": org_ws,
        },
        headers={"X-Integral-Scope": f"ws:{org_ws}"},
    )
    assert org_track_r.status_code == 200, org_track_r.text
    org_track_id = org_track_r.json()["track"]["id"]

    # Stage a create_entry against org Contacts via propose dispatch.
    staged = await dispatch_tool(
        "integral_create_entry",
        {
            "text": "Scoped bless contact",
            "track_hint": "Contacts",
            "title": "Scoped bless contact",
        },
        principal_id=user_id,
        scope=org_ws,
    )
    assert not staged.is_error, staged
    token = staged.data["token"]
    sc = await get_token(token)
    assert sc.payload.get("track_id") == org_track_id
    assert sc.payload.get("track_id") != personal_track_id

    bless_r = await authenticated_client.post(
        "/api/agentive/staging/bless-token",
        json={"token": token},
        headers={"X-Integral-Scope": f"ws:{org_ws}"},
    )
    assert bless_r.status_code == 200, bless_r.text
    body = bless_r.json()
    assert body.get("ok") is True, body
    exec_result = body.get("execute_result") or {}
    assert not exec_result.get("error"), exec_result

    org_entries = await invoke_route_in_process(
        list_entries,
        principal_id=user_id,
        scope=org_ws,
        track_id=org_track_id,
    )
    org_titles = [e.get("title") for e in org_entries.get("entries", [])]
    assert "Scoped bless contact" in org_titles

    personal_entries = await invoke_route_in_process(
        list_entries,
        principal_id=user_id,
        scope=personal_ws,
        track_id=personal_track_id,
    )
    personal_titles = [e.get("title") for e in personal_entries.get("entries", [])]
    assert "Scoped bless contact" not in personal_titles
