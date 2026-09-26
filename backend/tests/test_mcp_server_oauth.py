"""M2b Task 1 — OAuth AS + Resource-Server discovery and 401 surfaces.

These tests verify that jvspatial's M1 OAuth machinery is turned on for the
integral app via ``AuthConfig`` (``oauth_enabled`` + ``accept_oauth_bearer``):

  * the RFC 9728 Protected Resource Metadata document is served at root,
  * the RFC 8414 Authorization Server metadata document is served at root,
  * an unauthenticated request to an ``auth=True`` endpoint returns 401/403
    carrying an RFC 9728 §5.1 ``WWW-Authenticate`` challenge that points
    clients at the PRM discovery document.

The discovery documents mount at ROOT (``/.well-known/*``), not under the
``/api`` prefix; the OAuth token/authorize/register/revoke endpoints mount
under ``/api/oauth/*``. jvspatial auto-exempts both ``/.well-known/*`` and the
AS endpoints from bearer auth when ``oauth_enabled=True`` — no manual exempt
wiring on the integral side.

The unauthenticated client comes from the shared ``client`` fixture
(``tests/conftest.py``), which builds an httpx ASGITransport client with NO
Authorization header. ``TestAuthBypassMiddleware`` only pre-sets
``request.state.user`` when a valid ``Bearer`` token is present, so a
header-less request flows through to jvspatial's auth middleware and yields a
genuine 401 — exactly the surface MCP clients hit before they authenticate.
"""

import json

import pytest


@pytest.mark.asyncio
async def test_prm_discovery_served(client):
    """RFC 9728 Protected Resource Metadata is served at root, public."""
    resp = await client.get("/.well-known/oauth-protected-resource")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "resource" in body
    assert "authorization_servers" in body
    assert isinstance(body["authorization_servers"], list)
    assert body["authorization_servers"], "authorization_servers must be non-empty"


@pytest.mark.asyncio
async def test_as_metadata_served(client):
    """RFC 8414 Authorization Server metadata is served at root, public."""
    resp = await client.get("/.well-known/oauth-authorization-server")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "token_endpoint" in body
    assert "/oauth/token" in body["token_endpoint"], body["token_endpoint"]


@pytest.mark.asyncio
async def test_unauthenticated_401_has_www_authenticate(client):
    """An unauthenticated auth=True request returns 401/403 with a PRM challenge.

    ``GET /api/tracks`` is an ``auth=True`` list endpoint. With no Authorization
    header the request never gets a pre-set ``request.state.user`` (the test
    bypass only fires for valid bearers), so jvspatial's auth middleware denies
    it. Because ``accept_oauth_bearer=True`` and ``oauth_issuer_url`` is set, the
    middleware attaches a ``WWW-Authenticate`` challenge pointing at the PRM doc.
    """
    resp = await client.get("/api/tracks")
    assert resp.status_code in (401, 403), resp.text
    www = resp.headers.get("WWW-Authenticate") or resp.headers.get("www-authenticate")
    assert www is not None, dict(resp.headers)
    assert "resource_metadata" in www, www
    assert "/.well-known/oauth-protected-resource" in www, www


# ---------------------------------------------------------------------------
# M2b Task 3 — /api/mcp mount + session-manager lifespan wiring.
#
# The Streamable-HTTP ``StreamableHTTPSessionManager.handle_request`` raises
# ``RuntimeError: Task group is not initialized`` unless ``mgr.run()`` has been
# entered (it creates the anyio task group lazily inside ``run()``; even
# ``stateless=True`` requires it). main.py drives ``run()`` for the app lifetime
# by registering a startup hook (``__aenter__``) + shutdown hook (``__aexit__``)
# on the jvspatial ``server.lifecycle_manager`` — both fire inside the FastAPI
# lifespan jvspatial already installs (``LifecycleManager.lifespan``).
#
# httpx's ``ASGITransport`` (used by the shared ``client`` fixture) does NOT run
# lifespan, so the manager's task group would be uninitialized under that
# transport. The init test below wraps the app in ``asgi_lifespan.LifespanManager``
# so the lifespan — and thus the MCP startup hook — actually fires; only then
# does ``handle_request`` have an initialized task group.
# ---------------------------------------------------------------------------


def test_api_mcp_mount_present():
    """The ASGI app carries a ``Mount`` at ``/api/mcp`` (route exists, not 404).

    Pure structural assertion — no lifespan needed. Proves the mount landed on
    the live app object the server hands out.
    """
    from starlette.routing import Mount

    from tests.conftest import get_app

    app = get_app()
    mounts = [
        r for r in app.router.routes if isinstance(r, Mount) and r.path == "/api/mcp"
    ]
    assert mounts, [getattr(r, "path", r) for r in app.router.routes]


@pytest.mark.asyncio
async def test_api_mcp_unauthenticated_under_lifespan_is_401_not_taskgroup_error(
    bind_fresh_graph_context_for_async_tests,
):
    """Under a running lifespan, an UNAUTHENTICATED ``/api/mcp`` POST is a clean 401.

    This is the negative half of the init contract: it proves (a) the mount is
    present (NOT a Starlette 404 for an unrouted path) and (b) the request
    reached the auth middleware WITHOUT tripping
    ``RuntimeError: Task group is not initialized`` — i.e. the session manager's
    task group is initialized even on the rejected path. It also pins that
    ``/api/mcp`` stays auth-gated (NOT in ``auth_exempt_paths``).
    """
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport, AsyncClient

    from app import main as app_main
    from tests.conftest import get_app

    app = get_app()
    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "integral-test", "version": "0"},
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    async with LifespanManager(app):
        # The lifespan startup hook entered ``run()`` on the active manager ->
        # its task group is live. (``_mcp_active[0]`` is the instance the mount
        # routes at — rebuilt per lifespan cycle since run() is single-use.)
        assert app_main._mcp_active[0]._task_group is not None
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test", timeout=10.0
        ) as ac:
            resp = await ac.post(
                "/api/mcp", content=json.dumps(init_payload), headers=headers
            )

    assert resp.status_code != 404, (resp.status_code, resp.text)
    assert "Task group is not initialized" not in resp.text, resp.text
    # Auth-gated: no bearer -> 401/403 from the auth middleware, not a 200.
    assert resp.status_code in (401, 403), resp.text


@pytest.mark.asyncio
async def test_api_mcp_initialize_handshake_authenticated_under_lifespan(
    bind_fresh_graph_context_for_async_tests,
):
    """Under a running lifespan, an AUTHENTICATED ``initialize`` completes.

    Drives the real FastAPI lifespan via ``LifespanManager`` so the MCP
    session-manager ``run()`` startup hook fires. With a valid Bearer (the mount
    stays auth-gated) the JSON-RPC ``initialize`` POST must:

      * NOT 404 (mount present),
      * NOT surface ``RuntimeError: Task group is not initialized`` (manager
        initialized on the serving loop),
      * return a 200 JSON-RPC result carrying ``serverInfo`` (``json_response``
        means a JSON body, not an SSE stream).

    This is the positive proof that the mounted transport is wired end-to-end:
    the request traverses the full middleware stack, the auth gate, and the
    Streamable-HTTP transport with an initialized task group.
    """
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport, AsyncClient

    from app import main as app_main
    from tests.conftest import _bootstrap_test_user_fast, get_app

    token, _user = await _bootstrap_test_user_fast(
        email="mcp-init@example.com",
        password="testpassword123",
        name="MCP Init",
    )
    assert token, "bootstrap must yield a JWT for the auth gate"

    app = get_app()
    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "integral-test", "version": "0"},
        },
    }
    headers = {
        "Content-Type": "application/json",
        # Streamable HTTP requires the client advertise both content types.
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}",
    }

    async with LifespanManager(app):
        # The lifespan startup hook entered ``run()`` on the active manager ->
        # its task group is live.
        assert app_main._mcp_active[0]._task_group is not None

        transport = ASGITransport(app=app)
        # follow_redirects: a bare ``/api/mcp`` POST hits Starlette's Mount
        # trailing-slash 307 to ``/api/mcp/`` (method+body preserving). The MCP
        # transport serves the sub-app root; following the redirect reaches it.
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            timeout=10.0,
            follow_redirects=True,
        ) as ac:
            resp = await ac.post(
                "/api/mcp", content=json.dumps(init_payload), headers=headers
            )

    # Mount present (not a Starlette 404 for an unrouted path).
    assert resp.status_code != 404, (resp.status_code, resp.text)
    # The task-group RuntimeError would surface as a 500 carrying that text;
    # assert it did NOT.
    assert "Task group is not initialized" not in resp.text, resp.text
    # A successful ``initialize`` returns 200 with a JSON-RPC result carrying
    # serverInfo. (json_response=True -> a JSON body, not an SSE stream.)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("jsonrpc") == "2.0", body
    assert "result" in body, body
    assert "serverInfo" in body["result"], body


# ---------------------------------------------------------------------------
# M2b Task 4 — authenticated end-to-end ``tools/list`` + ``tools/call`` over the
# real Streamable-HTTP transport (the POSITIVE coverage T2/T3 left open).
#
# T2 proved the in-handler core fails closed (``_call_tool_impl`` with
# principal=None -> isError; ``_list`` -> []) as a UNIT, with identity passed in.
# T3 proved the middleware-level gate (unauthenticated ``/api/mcp`` -> 401) and
# that an authenticated ``initialize`` handshake reaches the transport (200 +
# serverInfo). Neither proved a real DISPATCH lands a result through the mounted,
# lifespan-driven, auth-gated path. These two tests close that: under a running
# lifespan and a real Bearer, a ``tools/list`` returns the catalogue and a
# ``tools/call integral_list_tracks`` dispatches all the way to the substrate and
# returns the seeded track — proving auth -> mount -> session manager -> handler
# -> resolve_principal_id -> dispatch_tool -> MCP response, end to end.
#
# HANDSHAKE APPROACH: raw JSON-RPC POSTs (not the SDK ``ClientSession``).
# ``build_session_manager`` builds the manager ``stateless=True``, and stateless
# mode makes every POST a fully independent request: the SDK creates a FRESH
# transport per request (``StreamableHTTPSessionManager._handle_stateless_request``)
# with ``mcp_session_id=None``, and the underlying ``ServerSession`` is seeded in
# ``InitializationState.Initialized`` (``session.py`` — ``stateless=True`` branch).
# Consequently a ``tools/list`` / ``tools/call`` POST needs NEITHER a prior
# ``initialize`` NOR a session-id echo NOR an SSE GET stream — ``_validate_session``
# short-circuits to True when ``mcp_session_id`` is None. A single self-contained
# POST per call is therefore the most robust driver (the full SDK client spins up
# anyio task groups + a standalone GET stream + a DELETE teardown that are brittle
# against ``ASGITransport`` with no real serving loop). We reuse T3's exact auth +
# transport setup (Bearer bootstrap, ``Accept: application/json, text/event-stream``,
# ``follow_redirects`` for the Mount trailing-slash 307, ``json_response=True`` so
# the body is JSON not SSE).
#
# GATING CONCLUSION (verified, no code change — the T4 security finding):
# unauthenticated ``/api/mcp`` is refused at the jvspatial auth-middleware layer
# (401) BEFORE the request ever reaches the MCP handler (proved by T3's
# ``test_api_mcp_unauthenticated_under_lifespan_is_401_not_taskgroup_error`` — a
# bearer-less POST never returns a JSON-RPC result, only a bare 401). The
# in-handler principal check (``_call_tool_impl`` principal=None -> isError;
# ``_list`` -> []) is the DEFENSE-IN-DEPTH backstop: it would fail closed even if
# a request somehow reached the handler without a principal (e.g. a future change
# that exempts the mount, or a direct in-process call). Two independent gates,
# middleware-first. T4 adds no new gating code; it confirms the authenticated path
# the gate lets through actually works.
# ---------------------------------------------------------------------------


async def _bootstrap_user_track_and_token(*, email: str):
    """Create an AuthUser + User + personal workspace + seeded track + a JWT.

    Returns ``(token, auth_user_id, workspace_id, track_id)``. Combines the
    dispatch test's inline graph bootstrap (``test_mcp_server_dispatch.
    _bootstrap_principal_and_track`` — real route handlers seed the track) with a
    password login so we also get the JWT the auth gate needs. ``auth_user_id`` is
    the AuthUser id == the JWT ``sub``; the track is owned by that principal, so
    the bearer the transport authenticates IS the track owner — proving
    ``resolve_principal_id`` yields the right user through the transport.
    """
    from jvspatial.api.auth.models import UserCreate, UserLogin

    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.auth import _get_auth_service
    from app.api.tracks import create_track
    from app.models.nodes import User
    from app.services.app_graph import catalog_user
    from app.services.personal_workspace import ensure_personal_workspace

    auth_service = _get_auth_service()
    user_response = await auth_service.register_user(
        UserCreate(email=email, password="testpassword123")
    )
    auth_user_id = user_response.id
    user_node = await User.create(user_id=auth_user_id, display_name="MCP E2E")
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

    token_response = await auth_service.login_user(
        UserLogin(email=email, password="testpassword123")
    )
    return token_response.access_token, auth_user_id, workspace_id, track_id


def _jsonrpc(method: str, params: dict | None = None, _id: int = 1) -> str:
    """Serialize one JSON-RPC 2.0 request frame."""
    return json.dumps(
        {"jsonrpc": "2.0", "id": _id, "method": method, "params": params or {}}
    )


@pytest.mark.asyncio
async def test_mcp_tools_list_authenticated(
    bind_fresh_graph_context_for_async_tests,
):
    """Under a running lifespan, an AUTHENTICATED ``tools/list`` returns the catalogue.

    Proves the authenticated principal sees the full M2a surface through the
    mounted transport (vs an unauthenticated request, which the handler lists as
    ``[]`` — see ``_list`` fail-closed in ``app.agentive.mcp.server``). Stateless
    mode means this single POST needs no prior ``initialize``.
    """
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport, AsyncClient

    from app import main as app_main
    from tests.conftest import get_app

    token, _auth_user_id, _workspace_id, _track_id = (
        await _bootstrap_user_track_and_token(email="mcp-list@example.com")
    )
    assert token, "bootstrap must yield a JWT for the auth gate"

    app = get_app()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}",
    }

    async with LifespanManager(app):
        assert app_main._mcp_active[0]._task_group is not None
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            timeout=15.0,
            follow_redirects=True,
        ) as ac:
            resp = await ac.post(
                "/api/mcp", content=_jsonrpc("tools/list"), headers=headers
            )

    assert resp.status_code == 200, resp.text
    assert "Task group is not initialized" not in resp.text, resp.text
    body = resp.json()
    assert body.get("jsonrpc") == "2.0", body
    assert "result" in body, body
    tools = body["result"].get("tools")
    assert isinstance(tools, list), body
    # The authenticated principal sees the full surface (vs [] unauthenticated).
    assert len(tools) >= 1, body
    names = {t["name"] for t in tools}
    assert "integral_query_spec" in names, names
    assert "integral_list_tracks" in names, names
    # Catalogue count tracks the dispatchable existing-status surface. Grew
    # 29 -> 32 when the resident->manifest migration wired integral_whoami +
    # integral_get_app and promoted integral_list_apps to existing, then
    # 32 -> 34 when integral_count_entries + integral_activity_digest were
    # wired as SERVICE-backed reads (agent_insights), then 34 -> 38 when Task 4
    # promoted integral_update_track / integral_delete_entry / integral_save_view
    # to existing and added integral_delete_track, then 38 when Task 5 added
    # integral_file_content, then 38 -> 50 with the tool_manifest expansion
    # (CUCS skill adoption + filing-agnostic rework), then 50 -> 54 with the
    # Routine Tasks feature (P_scheduling domain), then 54 -> 82 with the
    # manifest gap-tool reconciliation (skills-editor audit: 28 gap tools
    # promoted to existing and wired). If the catalogue legitimately grows
    # again, update this alongside test_mcp_server_dispatch.
    # 83 -> 82: integral_list_agents removed with the A2A discovery surface (ADR-003).
    # 82 -> 83: integral_attach_uploaded_file_to_entry added (Slice B — file a
    # chat-uploaded file into an entry).
    # 83 -> 86: integral_author_skill / integral_update_skill /
    # integral_delete_skill added (agent-facing skill authoring).
    # 86 -> 87: integral_delete_app added — no agent tool existed to delete an
    # app at all ("delete this app" via chat had nothing to call).
    # 87 -> 88: integral_propose_design added — the greenfield-build gate's
    # required propose step (records the design marker on the chat thread).
    # 88 -> 89: integral_attach_uploaded_image_to_entry added — on-demand image
    # attach (composer images are vision-only until filed onto an entry).
    # 98 -> 99: integral_transcribe_audio added — transcribes an audio
    # attachment with the bound workspace's speech-to-text provider.
    # 99 -> 104: workspace tools, routine hard-remove, and newly declared
    # Core tools reconciled into the catalogue.
    # 107 -> 110: session artifact upsert/get/list complete the persisted
    # greenfield design handoff.
    # 110 -> 111: one approved scaffold-plan build call.
    # 111 -> 112: register an App track template (W1.2 anchors).
    # 112 -> 113: check a design blueprint against the live palette (W1.4).
    assert len(tools) == 113, len(tools)


@pytest.mark.asyncio
async def test_mcp_tools_call_read_authenticated(
    bind_fresh_graph_context_for_async_tests,
):
    """Under a running lifespan, an AUTHENTICATED ``tools/call`` genuinely dispatches.

    Bootstraps a user + a seeded track, then drives ``tools/call
    integral_list_tracks`` over the transport. The seeded track must come back in
    the tool result, proving end-to-end dispatch under the OAuth-gated transport:
    the bearer's principal (== the track owner) flows through
    ``resolve_principal_id``, the ``X-Integral-Scope`` header resolves the seeded
    workspace, and ``dispatch_tool`` reads the real substrate. This is NOT a
    direct ``_call_tool_impl`` call (that is T2's unit) — it is the mounted,
    lifespan-driven, auth-gated path.
    """
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport, AsyncClient

    from app import main as app_main
    from tests.conftest import get_app

    token, _auth_user_id, workspace_id, _track_id = (
        await _bootstrap_user_track_and_token(email="mcp-call@example.com")
    )
    assert token, "bootstrap must yield a JWT for the auth gate"
    assert workspace_id, "bootstrap must yield a workspace for scope resolution"

    app = get_app()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}",
        # Pin the scope to the seeded workspace so the read is deterministic
        # (the resolver would also fall back to the user's personal workspace,
        # but the explicit header removes any ambiguity).
        "X-Integral-Scope": f"ws:{workspace_id}",
    }
    call_frame = _jsonrpc(
        "tools/call",
        {"name": "integral_list_tracks", "arguments": {}},
        _id=2,
    )

    async with LifespanManager(app):
        assert app_main._mcp_active[0]._task_group is not None
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            timeout=15.0,
            follow_redirects=True,
        ) as ac:
            resp = await ac.post("/api/mcp", content=call_frame, headers=headers)

    assert resp.status_code == 200, resp.text
    assert "Task group is not initialized" not in resp.text, resp.text
    body = resp.json()
    assert body.get("jsonrpc") == "2.0", body
    assert "result" in body, body
    result = body["result"]
    # A successful tool call is NOT an error envelope.
    assert result.get("isError") in (False, None), result
    # The read tool returns a dict -> the SDK surfaces it as structuredContent.
    structured = result.get("structuredContent")
    assert isinstance(structured, dict), result
    titles = [t.get("title") for t in structured.get("tracks", [])]
    assert "Seeded Track" in titles, structured


@pytest.mark.asyncio
async def test_mcp_tools_call_unauthenticated_refused_at_middleware(
    bind_fresh_graph_context_for_async_tests,
):
    """An UNAUTHENTICATED ``tools/call`` is refused at the auth middleware (401).

    The bearer-less POST never reaches the MCP handler — the jvspatial auth
    middleware rejects it first (no ``request.state.user`` -> 401/403), so the
    response is a bare HTTP error, NOT a JSON-RPC tool result. This pins the T4
    gating conclusion: middleware-first refusal is the primary gate; the
    in-handler principal check is the defense-in-depth backstop. (T3's
    ``test_api_mcp_unauthenticated_under_lifespan_is_401_not_taskgroup_error``
    covers the same property for ``initialize``; this asserts it for ``tools/call``
    specifically, the dispatch surface this task adds positive coverage for.)
    """
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport, AsyncClient

    from tests.conftest import get_app

    app = get_app()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    call_frame = _jsonrpc(
        "tools/call",
        {"name": "integral_list_tracks", "arguments": {}},
        _id=3,
    )

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            timeout=15.0,
            follow_redirects=True,
        ) as ac:
            resp = await ac.post("/api/mcp", content=call_frame, headers=headers)

    # Middleware-first refusal: a bare 401/403, never a 200 JSON-RPC result.
    assert resp.status_code in (401, 403), resp.text
    assert "Task group is not initialized" not in resp.text, resp.text


# ---------------------------------------------------------------------------
# M2b Task 5 — workspace scope-isolation (PC-2) over the OAuth-gated MCP
# transport. T4 proved an authenticated ``tools/call`` dispatches end-to-end;
# this proves the SCOPE the call runs under is the one ``X-Integral-Scope``
# names — AND that a non-member scope header cannot widen access.
#
# The lever is ``server._call`` resolving scope via
# ``resolve_workspace_id_from_request(req, principal_id)`` (request_scope.py):
# the ``X-Integral-Scope: ws:<id>`` header is honored ONLY when the principal is
# a live member of that workspace (step 1 of the resolver's priority chain);
# otherwise the resolver fails closed to the user's stored default / Personal
# Workspace (step 2/3). So:
#
#   * binding to a workspace the user owns FLIPS visibility (the header is the
#     only lever — steps 2-3 below), and
#   * binding to a workspace the user is NOT a member of does NOT widen — the
#     resolver discards the foreign hint and reads the user's own default
#     (step 4, the core PC-2 proof for the external MCP surface).
#
# Same transport+auth contract as T4: real ``LifespanManager`` lifespan so the
# session manager's task group is live, Bearer bootstrap for the auth gate,
# ``Accept: application/json, text/event-stream``, ``follow_redirects`` for the
# Mount trailing-slash 307, ``json_response=True`` so the body is JSON. The
# stateless manager makes each ``tools/call`` POST self-contained (no prior
# ``initialize`` / session-id echo) — see the T4 header comment.
# ---------------------------------------------------------------------------


async def _seed_second_workspace_with_track(
    *, auth_user_id: str, scope_ws_id: str, ws_name: str, track_title: str
) -> tuple[str, str]:
    """Create a second workspace the same user owns + a track in it.

    Mirrors ``tests/test_tooling_invoke.test_invoke_scope_isolation``'s WS_B
    bootstrap: ``create_workspace`` wires the caller as owner/member (so the
    resolver's membership check honors a header naming it), and ``create_track``
    takes an explicit ``workspace_id`` so the track placement is independent of
    any scope header. Returns ``(workspace_id, track_id)``.
    """
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.tracks import create_track
    from app.api.workspaces import create_workspace

    ws_result = await invoke_route_in_process(
        create_workspace,
        principal_id=auth_user_id,
        scope=scope_ws_id,
        name=ws_name,
    )
    assert not (isinstance(ws_result, dict) and ws_result.get("error")), ws_result
    ws_id = ws_result["workspace"]["id"]
    assert ws_id != scope_ws_id

    track_result = await invoke_route_in_process(
        create_track,
        principal_id=auth_user_id,
        scope=ws_id,
        title=track_title,
        visibility="private",
        workspace_id=ws_id,
    )
    assert not (
        isinstance(track_result, dict) and track_result.get("error")
    ), track_result
    return ws_id, track_result["track"]["id"]


@pytest.mark.asyncio
async def test_mcp_scope_isolation_over_transport(
    bind_fresh_graph_context_for_async_tests,
):
    """PC-2: ``X-Integral-Scope`` binds + validates over the MCP transport.

    Four properties, all driven through the mounted, lifespan-driven,
    auth-gated ``/api/mcp`` path (NOT ``_call_tool_impl`` directly):

      1. User-1 owns WS_A (Personal) and a second workspace WS_B, with TRACK_B
         seeded ONLY in WS_B.
      2. ``tools/call integral_list_tracks`` scoped to WS_A -> TRACK_B ABSENT.
      3. The same call scoped to WS_B -> TRACK_B PRESENT. The bound scope is the
         only lever; flipping it flips visibility -> the header genuinely binds
         through the transport.
      4. NO-WIDENING (the core PC-2 proof): a SECOND user owns WS_C with TRACK_C;
         user-1 is NOT a member of WS_C. ``tools/call`` as user-1 scoped to WS_C
         does NOT return TRACK_C — the resolver discards the foreign hint and
         fails closed to user-1's default. A non-member header cannot widen.
    """
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport, AsyncClient

    from app import main as app_main
    from tests.conftest import get_app

    # User-1: WS_A (Personal) + a seeded track. Reuse T4's bootstrap.
    token1, user1_id, ws_a, _track_a = await _bootstrap_user_track_and_token(
        email="mcp-scope-u1@example.com"
    )
    assert token1 and ws_a, "bootstrap must yield a JWT + a workspace"

    # WS_B: a second workspace user-1 owns, with TRACK_B seeded ONLY there.
    ws_b, track_b_id = await _seed_second_workspace_with_track(
        auth_user_id=user1_id,
        scope_ws_id=ws_a,
        ws_name="MCP Scope WS_B",
        track_title="Track B (WS_B only)",
    )

    # User-2: owns WS_C (their Personal workspace) with TRACK_C. User-1 is NOT a
    # member of WS_C -> a header naming it must not widen user-1's access.
    _token2, _user2_id, ws_c, track_c_id = await _bootstrap_user_track_and_token(
        email="mcp-scope-u2@example.com"
    )
    assert ws_c and ws_c not in (ws_a, ws_b), "WS_C must be a distinct foreign ws"

    app = get_app()

    def _headers(ws_id: str) -> dict:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token1}",
            "X-Integral-Scope": f"ws:{ws_id}",
        }

    def _structured(resp) -> dict:
        assert resp.status_code == 200, resp.text
        assert "Task group is not initialized" not in resp.text, resp.text
        body = resp.json()
        assert body.get("jsonrpc") == "2.0", body
        assert "result" in body, body
        result = body["result"]
        assert result.get("isError") in (False, None), result
        structured = result.get("structuredContent")
        assert isinstance(structured, dict), result
        return structured

    def _track_ids(structured: dict) -> set:
        return {t.get("id") for t in structured.get("tracks", [])}

    call_frame = _jsonrpc(
        "tools/call",
        {"name": "integral_list_tracks", "arguments": {}},
        _id=4,
    )

    async with LifespanManager(app):
        assert app_main._mcp_active[0]._task_group is not None
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            timeout=20.0,
            follow_redirects=True,
        ) as ac:
            # 2. Bound to WS_A -> TRACK_B absent (it lives in WS_B).
            resp_a = await ac.post(
                "/api/mcp", content=call_frame, headers=_headers(ws_a)
            )
            # 3. Bound to WS_B -> TRACK_B present (only the binding changed).
            resp_b = await ac.post(
                "/api/mcp", content=call_frame, headers=_headers(ws_b)
            )
            # 4. Bound to WS_C (foreign, non-member) -> must NOT widen.
            resp_c = await ac.post(
                "/api/mcp", content=call_frame, headers=_headers(ws_c)
            )

    # 2 + 3: the header is the only lever; flipping it flips TRACK_B visibility.
    structured_a = _structured(resp_a)
    assert track_b_id not in _track_ids(structured_a), structured_a

    structured_b = _structured(resp_b)
    assert track_b_id in _track_ids(structured_b), structured_b

    # 4: NO-WIDENING. Since the SM3 fix the resolver REFUSES a foreign scope
    # header outright instead of quietly discarding it and running under
    # user-1's own default. Either way TRACK_C (which lives in user-2's WS_C)
    # is never returned — this is the PC-2 proof for the external MCP surface:
    # a non-member scope header cannot widen. The refusal is the stronger
    # outcome because it also stops the caller from mistaking their own
    # workspace's contents for WS_C's.
    assert resp_c.status_code == 200, resp_c.text
    body_c = resp_c.json()
    result_c = body_c["result"]
    assert result_c.get("isError") is True, body_c
    assert track_c_id not in resp_c.text, resp_c.text


@pytest.mark.asyncio
async def test_non_member_scope_header_is_refused_not_widened(
    bind_fresh_graph_context_for_async_tests,
):
    """Unit backstop for PC-2: the resolver refuses a non-member scope hint.

    Direct assertion on ``resolve_workspace_id_from_request`` (the lever the MCP
    ``_call`` handler uses): user-1 sending ``X-Integral-Scope: ws:<WS_C>`` for a
    workspace they do NOT belong to never resolves to WS_C. Before the SM3 fix
    it silently resolved to user-1's OWN default instead; it now raises
    ``InsufficientPermissionsError``. Either way no widening happens — the
    raise is strictly stronger, and additionally stops a client from reading
    its own workspace while believing it read WS_C. (A valid member header, by
    contrast, still resolves to the named workspace — the positive half of
    step 1.)
    """
    from jvspatial.api.exceptions import InsufficientPermissionsError

    from app.services.request_scope import resolve_workspace_id_from_request

    _token1, user1_id, ws_a, _track_a = await _bootstrap_user_track_and_token(
        email="mcp-scope-resolver-u1@example.com"
    )
    # User-1 owns WS_B too -> a header naming it is honored (positive control).
    ws_b, _track_b = await _seed_second_workspace_with_track(
        auth_user_id=user1_id,
        scope_ws_id=ws_a,
        ws_name="Resolver WS_B",
        track_title="Resolver Track B",
    )
    # User-2 owns WS_C; user-1 is NOT a member.
    _token2, _user2_id, ws_c, _track_c = await _bootstrap_user_track_and_token(
        email="mcp-scope-resolver-u2@example.com"
    )
    assert ws_c not in (ws_a, ws_b)

    class _Req:
        """Minimal Starlette-request shape the resolver reads (headers + state)."""

        def __init__(self, scope_ws: str):
            self.headers = {"x-integral-scope": f"ws:{scope_ws}"}

            class _State:
                pass

            self.state = _State()

    # Member header (WS_B) -> honored.
    resolved_member = await resolve_workspace_id_from_request(_Req(ws_b), user1_id)
    assert resolved_member == ws_b, resolved_member

    # Non-member header (WS_C) -> refused outright; never binds to WS_C.
    with pytest.raises(InsufficientPermissionsError):
        await resolve_workspace_id_from_request(_Req(ws_c), user1_id)


# ---------------------------------------------------------------------------
# M2b hardening — boot-time HTTPS-issuer guard.
#
# ``OAUTH_ISSUER_URL`` defaults to ``http://localhost:4000`` and is stamped into
# token ``iss``/``aud``, the AS/PRM discovery metadata, and the RFC 9728
# ``WWW-Authenticate`` pointer. An ``http://`` issuer on a PUBLIC origin in
# production is a downgrade/interception footgun, so ``app.main`` runs an
# import-time guard (mirroring the SECRET_KEY guard) that refuses to boot in a
# non-dev environment when the issuer is insecure. The guard delegates to the
# pure predicate ``_validate_oauth_issuer`` so the policy is unit-testable
# without driving the import-time ``sys.exit``. RFC 8252 loopback hosts
# (localhost / 127.0.0.1 / [::1]) keep the ``http://`` exception always, matching
# M1's DCR loopback-redirect policy; dev (DEBUG/TESTING) is exempt entirely so
# the local default still boots.
# ---------------------------------------------------------------------------


def test_oauth_issuer_predicate_rejects_insecure_public_origin_in_prod():
    """A non-loopback ``http://`` issuer is refused when not in dev."""
    from app.main import _validate_oauth_issuer

    assert _validate_oauth_issuer("http://api.example.com", is_dev=False) is False
    assert _validate_oauth_issuer("http://api.example.com:8080", is_dev=False) is False


def test_oauth_issuer_predicate_accepts_https_in_prod():
    """An ``https://`` issuer is always acceptable."""
    from app.main import _validate_oauth_issuer

    assert _validate_oauth_issuer("https://api.example.com", is_dev=False) is True
    assert _validate_oauth_issuer("https://api.example.com:8443", is_dev=False) is True


def test_oauth_issuer_predicate_accepts_loopback_http_in_prod():
    """RFC 8252 loopback hosts keep the ``http://`` exception even in prod."""
    from app.main import _validate_oauth_issuer

    assert _validate_oauth_issuer("http://localhost:4000", is_dev=False) is True
    assert _validate_oauth_issuer("http://localhost", is_dev=False) is True
    assert _validate_oauth_issuer("http://127.0.0.1:4000", is_dev=False) is True
    assert _validate_oauth_issuer("http://[::1]:4000", is_dev=False) is True


def test_oauth_issuer_predicate_loopback_match_is_exact_host_not_prefix():
    """A spoofed ``localhost.evil.com`` must NOT be treated as loopback."""
    from app.main import _validate_oauth_issuer

    assert _validate_oauth_issuer("http://localhost.evil.com", is_dev=False) is False
    assert _validate_oauth_issuer("http://127.0.0.1.evil.com", is_dev=False) is False


def test_oauth_issuer_predicate_dev_accepts_anything():
    """In dev the guard is inert — any issuer (incl. the local default) boots."""
    from app.main import _validate_oauth_issuer

    assert _validate_oauth_issuer("http://localhost:4000", is_dev=True) is True
    assert _validate_oauth_issuer("http://api.example.com", is_dev=True) is True
    assert _validate_oauth_issuer("garbage", is_dev=True) is True


def test_oauth_issuer_predicate_rejects_empty_or_unknown_scheme_in_prod():
    """Empty / scheme-less / non-http(s) issuers are refused outside dev."""
    from app.main import _validate_oauth_issuer

    assert _validate_oauth_issuer("", is_dev=False) is False
    assert _validate_oauth_issuer("api.example.com", is_dev=False) is False
    assert _validate_oauth_issuer("ftp://api.example.com", is_dev=False) is False


# ---------------------------------------------------------------------------
# M3 hardening — boot-time FRONTEND_ORIGIN guard.
#
# ``FRONTEND_ORIGIN`` is the SPA origin jvspatial 302-redirects an
# unauthenticated ``GET /api/oauth/authorize`` browser to (the OAuth login leg
# of the consent flow). An empty value or an ``http://`` origin on a public
# (non-loopback) host points the login redirect at a plaintext / unset target —
# a downgrade/interception footgun. ``app.main`` runs an import-time guard
# (mirroring the OAUTH_ISSUER_URL guard) that refuses to boot in a non-dev
# environment when the origin is insecure. The guard reuses the SAME pure
# predicate ``_validate_oauth_issuer`` so the policy is identical and
# unit-testable without driving the import-time ``sys.exit``: https always OK,
# http only for RFC 8252 loopback hosts, empty/scheme-less rejected, dev exempt.
# ---------------------------------------------------------------------------


def test_frontend_origin_guard_accepts_https_and_loopback_http_in_prod():
    """The SPA origin guard accepts https + the local loopback http default."""
    from app.main import _validate_oauth_issuer

    # The local dev default must still pass the loopback-http exception.
    assert _validate_oauth_issuer("http://localhost:9006", is_dev=False) is True
    assert _validate_oauth_issuer("https://app.example.com", is_dev=False) is True
    assert _validate_oauth_issuer("http://127.0.0.1:9006", is_dev=False) is True


def test_frontend_origin_guard_rejects_empty_or_public_http_in_prod():
    """An empty value or a public http:// SPA origin is refused outside dev."""
    from app.main import _validate_oauth_issuer

    assert _validate_oauth_issuer("", is_dev=False) is False
    assert _validate_oauth_issuer("http://app.example.com", is_dev=False) is False
    # Spoofed loopback prefix must not slip through.
    assert _validate_oauth_issuer("http://localhost.evil.com", is_dev=False) is False


def test_frontend_origin_guard_dev_accepts_anything():
    """In dev the SPA-origin guard is inert — any origin (incl. empty) boots."""
    from app.main import _validate_oauth_issuer

    assert _validate_oauth_issuer("http://localhost:9006", is_dev=True) is True
    assert _validate_oauth_issuer("http://app.example.com", is_dev=True) is True
    assert _validate_oauth_issuer("", is_dev=True) is True
