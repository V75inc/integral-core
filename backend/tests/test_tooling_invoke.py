"""invoke_route_in_process calls a route handler in-process under a principal.

The invoker synthesizes a minimal Starlette-Request-shaped object exposing
``request.state.user`` (read by ``resolve_principal_id``) plus an
``X-Integral-Scope`` header, then calls the route handler directly. The
route's own auth/policy/scope code runs unchanged.
"""

import pytest

from app.agentive.tooling.invoke import invoke_route_in_process


async def _bootstrap_principal_and_track():
    """Create an AuthUser + User + personal workspace, then a track in it.

    Mirrors conftest's ``_bootstrap_test_user_fast`` (no HTTP signup) and uses
    the real ``create_track`` endpoint handler in-process so the seeded track
    is reachable by the same code path ``list_tracks`` reads.

    Returns ``(auth_user_id, workspace_id)``.
    """
    from jvspatial.api.auth.models import UserCreate

    from app.api.auth import _get_auth_service
    from app.api.tracks import create_track
    from app.models.nodes import User
    from app.services.app_graph import catalog_user
    from app.services.personal_workspace import ensure_personal_workspace

    auth_service = _get_auth_service()
    user_response = await auth_service.register_user(
        UserCreate(email="invoke-test@example.com", password="testpassword123")
    )
    auth_user_id = user_response.id
    user_node = await User.create(user_id=auth_user_id, display_name="Invoke Test")
    await catalog_user(user_node)
    ws = await ensure_personal_workspace(user_node)
    workspace_id = ws.id if ws else None

    # Seed a track via the real handler, scoped to the personal workspace.
    created = await invoke_route_in_process(
        create_track,
        principal_id=auth_user_id,
        scope=workspace_id,
        title="Seeded Track",
        visibility="private",
        workspace_id=workspace_id,
    )
    assert not (isinstance(created, dict) and created.get("error")), created
    return auth_user_id, workspace_id


@pytest.mark.asyncio
async def test_invoke_list_tracks(bind_fresh_graph_context_for_async_tests):
    """Invoking the real GET /api/tracks handler in-process returns a payload."""
    from app.api.tracks import list_tracks

    auth_user_id, workspace_id = await _bootstrap_principal_and_track()

    result = await invoke_route_in_process(
        list_tracks, principal_id=auth_user_id, scope=workspace_id
    )

    assert isinstance(result, dict)
    assert "tracks" in result
    titles = [t.get("title") for t in result["tracks"]]
    assert "Seeded Track" in titles


def _track_ids_in_result(result):
    """Extract track ids from a list_tracks result payload, robustly.

    The handler returns ``{"tracks": [...]}`` today, but be tolerant of a
    bare list too — the assertion below should gate on scope binding, not on
    the exact envelope shape.
    """
    if isinstance(result, dict):
        rows = result.get("tracks", [])
    elif isinstance(result, list):
        rows = result
    else:
        rows = []
    return {t.get("id") for t in rows if isinstance(t, dict)}


@pytest.mark.asyncio
async def test_invoke_scope_isolation(bind_fresh_graph_context_for_async_tests):
    """Negative control: list_tracks visibility tracks the BOUND scope.

    Unlike ``test_invoke_list_tracks`` (which seeds into the same Personal
    Workspace the fallback resolves to), this seeds a track in a SECOND
    workspace (WS_B) and proves the only lever that flips its visibility is
    the ``X-Integral-Scope`` binding:

      * bound to WS_A → TRACK_B absent;
      * bound to WS_B → TRACK_B present.

    If the scope header were dropped/ignored, the resolver would fail-closed
    to the Personal Workspace (WS_A) for BOTH calls, so the ``scope=WS_B``
    assertion below would fail — which is exactly what makes this test gate
    on scope binding rather than mere structural compatibility.
    """
    from app.api.tracks import create_track, list_tracks

    auth_user_id, ws_a = await _bootstrap_principal_and_track()

    # WS_B: a second workspace the same user owns. Created via the real
    # create_workspace handler in-process; the caller is wired as owner via
    # IS_MEMBER_OF, so the scope resolver's membership check (step 1 of
    # request_scope.resolve_workspace_id_from_request) honors the header.
    from app.api.workspaces import create_workspace

    ws_b_result = await invoke_route_in_process(
        create_workspace,
        principal_id=auth_user_id,
        scope=ws_a,
        name="Scope Isolation WS_B",
    )
    assert not (isinstance(ws_b_result, dict) and ws_b_result.get("error")), ws_b_result
    ws_b = ws_b_result["workspace"]["id"]
    assert ws_b != ws_a

    # TRACK_B lives in WS_B (create_track takes an explicit workspace_id;
    # it does NOT read the scope header — so the track placement is
    # independent of the binding under test).
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
    track_b_id = track_b_result["track"]["id"]

    # Bound to WS_A: TRACK_B must NOT appear (it lives in WS_B).
    scoped_a = await invoke_route_in_process(
        list_tracks, principal_id=auth_user_id, scope=ws_a
    )
    assert track_b_id not in _track_ids_in_result(scoped_a), scoped_a

    # Bound to WS_B: TRACK_B must appear. The ONLY thing that changed is the
    # bound scope — flipping visibility proves the header genuinely binds.
    scoped_b = await invoke_route_in_process(
        list_tracks, principal_id=auth_user_id, scope=ws_b
    )
    assert track_b_id in _track_ids_in_result(scoped_b), scoped_b


@pytest.mark.asyncio
async def test_invoke_unknown_user_returns_error_envelope(
    bind_fresh_graph_context_for_async_tests,
):
    """A missing principal yields a structured 401 envelope, not an exception."""
    from app.api.tracks import list_tracks

    result = await invoke_route_in_process(
        list_tracks, principal_id="n.User.does-not-exist"
    )

    assert isinstance(result, dict)
    assert result.get("error") is True
    assert result.get("status_code") == 401
    assert result.get("error_code") == "user_not_found"
