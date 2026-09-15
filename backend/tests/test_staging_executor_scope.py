"""B-AGENT-03 — staging executors reject targets outside active workspace."""

from __future__ import annotations

import pytest

from app.services.agent_scope import current_scope_workspace_id


class _T:
    def __init__(self, id_: str, workspace_id: str):
        self.id = id_
        self.title = "Track"
        self.title_fold = "track"
        self.workspace_id = workspace_id


W2 = "n.Workspace.beta"
IN_SCOPE = _T("n.Track.in-scope", W2)
FOREIGN = "n.Track.foreign-w1"


@pytest.fixture
def patched_scoped_tracks(monkeypatch):
    async def fake_tracks(user_id: str):
        return [IN_SCOPE]

    monkeypatch.setattr(
        "app.services.permissions.get_user_accessible_tracks", fake_tracks
    )


@pytest.mark.asyncio
async def test_dispatch_create_entry_rejects_foreign_track(patched_scoped_tracks):
    from app.agentive.staging_executors import dispatch

    token = current_scope_workspace_id.set(W2)
    try:
        result = await dispatch(
            user_id="u1",
            kind="create_entry",
            payload={"track_id": FOREIGN, "title": "Should not apply"},
        )
        assert result.get("error") is True
        assert result.get("error_code") == "scope_violation"
    finally:
        current_scope_workspace_id.reset(token)


@pytest.mark.asyncio
async def test_validate_kind_scope_passes_in_scope_track(patched_scoped_tracks):
    from app.agentive.staging_executors import _validate_kind_scope

    token = current_scope_workspace_id.set(W2)
    try:
        err = await _validate_kind_scope(
            "create_entry",
            {"track_id": IN_SCOPE.id, "title": "OK"},
            user_id="u1",
        )
        assert err is None
    finally:
        current_scope_workspace_id.reset(token)


# ---------------------------------------------------------------------------
# June 29 QA #2 — a mid-batch app/track create must drop the caller's
# accessible-set caches so a LATER op's scope check sees the new resource.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_invalidates_access_cache_after_create_track(monkeypatch):
    from app.agentive import staging_executors as se

    calls: list = []

    async def fake_exec(user_id, payload):
        return {"track": {"id": "n.Track.new"}}

    monkeypatch.setitem(se._EXECUTORS, "create_track", fake_exec)
    monkeypatch.setattr(
        "app.services.permissions.invalidate_user_accessible_caches",
        lambda uid: calls.append(uid),
    )
    # No active scope → _validate_kind_scope is a no-op (create_track has no
    # track_id to check anyway); we're asserting the post-success invalidation.
    res = await se.dispatch(user_id="u9", kind="create_track", payload={"name": "T"})
    assert res == {"track": {"id": "n.Track.new"}}
    assert calls == ["u9"], "create_track must invalidate the caller's access cache"


@pytest.mark.asyncio
async def test_dispatch_skips_cache_invalidation_for_benign_kind(monkeypatch):
    from app.agentive import staging_executors as se

    calls: list = []

    async def fake_exec(user_id, payload):
        return {"ok": True}

    monkeypatch.setitem(se._EXECUTORS, "add_comment", fake_exec)
    monkeypatch.setattr(
        "app.services.permissions.invalidate_user_accessible_caches",
        lambda uid: calls.append(uid),
    )
    await se.dispatch(
        user_id="u9", kind="add_comment", payload={"entry_id": "e1", "body": "hi"}
    )
    assert calls == [], "non-access-mutating kinds must not thrash the cache"


@pytest.mark.asyncio
async def test_dispatch_no_invalidation_when_create_errors(monkeypatch):
    from app.agentive import staging_executors as se

    calls: list = []

    async def fake_exec(user_id, payload):
        return {"error": True, "message": "nope"}

    monkeypatch.setitem(se._EXECUTORS, "create_app", fake_exec)
    monkeypatch.setattr(
        "app.services.permissions.invalidate_user_accessible_caches",
        lambda uid: calls.append(uid),
    )
    await se.dispatch(user_id="u9", kind="create_app", payload={"name": "X"})
    assert calls == [], "a failed create must not invalidate the cache"


# ---------------------------------------------------------------------------
# Mint-time workspace capture — an org write staged in Acme must execute in
# Acme even when the FE bless request arrives without a scope header (which
# would otherwise resolve to the caller's personal workspace).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_under_request_scope_prefers_minted_workspace(monkeypatch):
    """``preferred_workspace_id`` wins over the request-resolved scope."""
    from app.agentive import staging_executors as se

    seen: dict = {}

    async def fake_dispatch(*, user_id, kind, payload):
        seen["ws"] = current_scope_workspace_id.get()
        return {"ok": True}

    async def fake_resolve(request, user_id):
        return "n.Workspace.personal"  # what a header-less request falls back to

    monkeypatch.setattr(se, "dispatch", fake_dispatch)
    monkeypatch.setattr(se, "resolve_executor_workspace_id", fake_resolve)

    await se.dispatch_under_request_scope(
        request=object(),
        user_id="u1",
        kind="create_app",
        payload={"name": "X"},
        preferred_workspace_id="n.Workspace.acme",
    )
    assert seen["ws"] == "n.Workspace.acme"


@pytest.mark.asyncio
async def test_dispatch_under_request_scope_falls_back_when_unminted(monkeypatch):
    """Legacy tokens (no captured workspace) still use the request scope."""
    from app.agentive import staging_executors as se

    seen: dict = {}

    async def fake_dispatch(*, user_id, kind, payload):
        seen["ws"] = current_scope_workspace_id.get()
        return {"ok": True}

    async def fake_resolve(request, user_id):
        return "n.Workspace.fromrequest"

    monkeypatch.setattr(se, "dispatch", fake_dispatch)
    monkeypatch.setattr(se, "resolve_executor_workspace_id", fake_resolve)

    await se.dispatch_under_request_scope(
        request=object(),
        user_id="u1",
        kind="create_app",
        payload={"name": "X"},
        preferred_workspace_id=None,
    )
    assert seen["ws"] == "n.Workspace.fromrequest"


@pytest.mark.asyncio
async def test_create_staged_change_captures_active_workspace():
    """The mint records the agent's active workspace for execution."""
    from app.agentive import staging

    tok = current_scope_workspace_id.set("n.Workspace.acme")
    try:
        sc = await staging.create_staged_change(
            user_id="u1",
            session_id="s1",
            kind="create_app",
            summary="Create app X",
            diff_human="Create app X",
            diff_machine={},
            payload={"name": "X"},
        )
        assert sc.workspace_id == "n.Workspace.acme"
    finally:
        current_scope_workspace_id.reset(tok)


# ---------------------------------------------------------------------------
# create_app / create_track must forward the bound workspace. ``resolve_
# workspace_id`` defaults a missing workspace to the caller's PERSONAL space
# (ignoring request scope), so without this every agent-staged app/track
# landed in personal — invisible from the org workspace it was staged in.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_x_create_app_forwards_active_workspace(monkeypatch):
    from app.agentive import staging_executors as se

    seen: dict = {}

    async def fake_call(handler, user_id, **kwargs):
        seen.update(kwargs)
        return {"app": {"id": "n.WorkspaceApp.x"}}

    monkeypatch.setattr(se, "_call_endpoint", fake_call)
    tok = current_scope_workspace_id.set("n.Workspace.acme")
    try:
        await se._x_create_app("u1", {"name": "Docs"})
    finally:
        current_scope_workspace_id.reset(tok)
    assert seen.get("workspace_id") == "n.Workspace.acme"
    assert seen.get("name") == "Docs"


@pytest.mark.asyncio
async def test_x_create_track_standalone_forwards_active_workspace(monkeypatch):
    from app.agentive import staging_executors as se

    seen: dict = {}

    async def fake_call(handler, user_id, **kwargs):
        seen.update(kwargs)
        return {"track": {"id": "n.Track.x"}}

    monkeypatch.setattr(se, "_call_endpoint", fake_call)
    tok = current_scope_workspace_id.set("n.Workspace.acme")
    try:
        await se._x_create_track("u1", {"title": "T"})
    finally:
        current_scope_workspace_id.reset(tok)
    assert seen.get("workspace_id") == "n.Workspace.acme"


@pytest.mark.asyncio
async def test_x_create_track_under_app_inherits_app_workspace(monkeypatch):
    """A track with ``app_id`` inherits the app's workspace — no override."""
    from app.agentive import staging_executors as se

    seen: dict = {}

    async def fake_call(handler, user_id, **kwargs):
        seen.update(kwargs)
        return {"track": {"id": "n.Track.x"}}

    monkeypatch.setattr(se, "_call_endpoint", fake_call)
    tok = current_scope_workspace_id.set("n.Workspace.acme")
    try:
        await se._x_create_track("u1", {"title": "T", "app_id": "n.WorkspaceApp.a"})
    finally:
        current_scope_workspace_id.reset(tok)
    assert seen.get("app_id") == "n.WorkspaceApp.a"
    assert "workspace_id" not in seen
