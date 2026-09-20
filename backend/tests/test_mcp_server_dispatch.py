"""Lowlevel MCP server: catalogue listing + dispatch routing (M2b Task 2).

These exercise the transport-agnostic core of the MCP server built in
``app.agentive.mcp.server`` WITHOUT standing up a Streamable-HTTP transport or a
real request context. The two module-level impls — ``_list_tools_impl`` and
``_call_tool_impl`` — are unit-testable directly: identity (``principal_id``) and
scope are passed in explicitly, exactly as the request-context-bound handlers
pass them through after reading ``resolve_principal_id`` /
``resolve_workspace_id_from_request``.

The defining contracts proved here:

* The list surface returns the full M2a catalogue (29 tools) as ``types.Tool``
  with non-empty ``inputSchema``.
* Dispatch is FAIL-CLOSED: no principal -> a ``CallToolResult(isError=True)``,
  never a dispatch.
* A read tool dispatches end-to-end and returns a dict (structured content).
* An unknown tool -> ``CallToolResult(isError=True)``.
* A propose tool STAGES (token + ``_kind == "staged_change"``) and does NOT
  apply the write (staged-not-applied).
* ``build_mcp_server()`` returns an ``MCPServer`` with the list/call handlers
  registered.

The inline bootstrap mirrors ``tests/test_tooling_dispatch_read`` /
``test_tooling_dispatch_propose`` (no HTTP signup; real route handlers seed the
graph) so the MCP server routes through the exact substrate the dispatch tests
cover.
"""

import mcp.types as types
import pytest
from jsonschema import Draft202012Validator

from app.agentive.mcp.server import (
    _call_tool_impl,
    _list_tools_impl,
    build_mcp_server,
)


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
        UserCreate(email="mcp-test@example.com", password="testpassword123")
    )
    auth_user_id = user_response.id
    user_node = await User.create(user_id=auth_user_id, display_name="MCP Test")
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


@pytest.fixture(autouse=True)
def _reset_staging_store():
    """Clear the module-global in-memory staging store around each test."""
    from app.agentive.staging import _reset_for_tests

    _reset_for_tests()
    yield
    _reset_for_tests()


@pytest.mark.asyncio
async def test_list_tools_returns_catalogue():
    """``_list_tools_impl`` exposes the full M2a catalogue as ``types.Tool``."""
    tools = await _list_tools_impl()
    # 29 -> 32 with the resident->manifest migration (integral_whoami +
    # integral_get_app wired, integral_list_apps promoted to existing).
    # 32 -> 34 when integral_count_entries + integral_activity_digest were
    # wired as SERVICE-backed reads (agent_insights).
    # 34 -> 38 when Task 4 promoted integral_update_track / integral_delete_entry
    # / integral_save_view to existing and added integral_delete_track (4 new
    # dispatchable propose tools).
    # 38 when Task 5 added integral_file_content (fused classify→stage).
    # 38 -> 50 with the tool_manifest expansion (CUCS skill adoption +
    # filing-agnostic rework): organize/onboard/model/review/scaffold SOPs and
    # their supporting tools (batch ops, tags, attachments, update_app, …).
    # 50 -> 54 with the Routine Tasks feature (P_scheduling domain):
    # integral_schedule_task / integral_list_routines / integral_update_routine
    # / integral_cancel_routine.
    # 54 -> 82 (skills-editor gap-tool reconciliation), then 82 -> 83 with the
    # June 23/29 QA tools (whole-track attachment listing, etc.):
    # 28 gap tools promoted to existing and wired — collaborator/invite/share-link
    # /exclusion mgmt, comment edit/delete, view delete/export, notifications,
    # sync conflicts, audit log, cross-track search, and the get_scope/get_access
    # /get_feed/get_related/list_* read fills.
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
    # 98 -> 99: integral_transcribe_audio added — read tool that transcribes
    # an audio attachment with the bound workspace's speech-to-text provider.
    # 99 -> 102: integral_call_workspace_tool + related catalogue growth on
    # dev, plus integral_delete_routine (Inbox hard-remove; soft cancel was
    # already integral_cancel_routine).
    # 102 -> 104: newly declared Core tools reconciled into the catalogue.
    # 104 -> 105: bounded, provenance-bearing Core QuerySpec read.
    # 107 -> 110: session artifact upsert/get/list complete the persisted
    # greenfield design handoff.
    assert len(tools) == 110, len(tools)
    assert all(isinstance(t, types.Tool) for t in tools)

    names = {t.name for t in tools}
    assert "integral_query_spec" in names
    assert "integral_list_tracks" in names
    assert "integral_create_entry" in names

    # Every tool carries a non-empty JSON-schema inputSchema (camelCase field).
    for t in tools:
        assert isinstance(t.inputSchema, dict), t.name
        assert t.inputSchema, t.name
        assert t.description, t.name


@pytest.mark.asyncio
async def test_query_spec_catalogue_schema_is_complete_and_closed():
    """Schema-enforcing MCP clients accept bounded plans and reject open fields."""
    tools = {tool.name: tool for tool in await _list_tools_impl()}
    schema = tools["integral_query_spec"].inputSchema
    validator = Draft202012Validator(schema)
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False

    validator.validate(
        {
            "spec": {
                "resource": "entry",
                "select": ["id", "title"],
                "filters": [{"field": "status", "op": "eq", "value": "open"}],
                "sort": [{"field": "updated_at", "direction": "desc"}],
                "traversal": [
                    {
                        "edge": "references",
                        "select": ["id"],
                        "direction": "out",
                        "depth": 1,
                        "limit": 5,
                    }
                ],
                "limit": 20,
                "cost_ceiling": 100,
            }
        }
    )
    for invalid in (
        {"spec": {"resource": "workspace", "select": ["id"]}},
        {"spec": {"resource": "entry", "select": ["id"], "unexpected": True}},
        {"spec": {"resource": "entry", "select": []}},
        {
            "spec": {
                "resource": "entry",
                "select": ["id"],
                "filters": [{"field": "status", "op": "matches", "value": "x"}],
            }
        },
        {"spec": {"resource": "entry", "select": ["name"]}},
        {
            "spec": {
                "resource": "entry",
                "select": ["id"],
                "traversal": [{"edge": "tracks", "select": ["id"]}],
            }
        },
        {
            "spec": {
                "resource": "entry",
                "select": ["id"],
                "traversal": [{"edge": "track", "direction": "out", "select": ["id"]}],
            }
        },
        {
            "spec": {
                "resource": "entry",
                "select": ["id"],
                "filters": [{"field": "status", "op": "is_null"}],
            }
        },
        {"spec": {"resource": "entry", "select": ["id"]}, "open": True},
    ):
        assert list(validator.iter_errors(invalid)), invalid


@pytest.mark.asyncio
async def test_call_tool_no_principal_fails_closed():
    """No authenticated principal -> isError result, NEVER a dispatch."""
    res = await _call_tool_impl(
        "integral_list_tracks", {}, principal_id=None, scope=None
    )
    assert isinstance(res, types.CallToolResult)
    assert res.isError is True
    # No structured data leaked on the fail-closed path.
    assert res.structuredContent is None
    # The error text names the unauthenticated cause.
    text = " ".join(c.text for c in res.content if isinstance(c, types.TextContent))
    assert "unauthorized" in text.lower()


@pytest.mark.asyncio
async def test_call_tool_read_dispatches(bind_fresh_graph_context_for_async_tests):
    """A read tool dispatches under a principal+scope and returns a dict.

    The SDK ``call_tool`` decorator turns a returned ``dict`` into
    ``structuredContent``; this impl returns that dict directly. The seeded
    track must appear in the result.
    """
    auth_user_id, workspace_id, _track_id = await _bootstrap_principal_and_track()

    res = await _call_tool_impl(
        "integral_list_tracks",
        {},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    # Non-error read returns a plain dict (becomes structuredContent downstream).
    assert isinstance(res, dict), res
    titles = [t.get("title") for t in res.get("tracks", [])]
    assert "Seeded Track" in titles, res


@pytest.mark.asyncio
async def test_call_query_spec_returns_provenance_and_receipt(
    bind_fresh_graph_context_for_async_tests,
):
    """MCP QuerySpec calls return live rows plus provenance and receipt links."""
    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()

    res = await _call_tool_impl(
        "integral_query_spec",
        {
            "spec": {
                "resource": "track",
                "select": ["id", "title"],
                "filters": [{"field": "id", "op": "eq", "value": track_id}],
                "limit": 1,
            }
        },
        principal_id=auth_user_id,
        scope=workspace_id,
    )

    assert isinstance(res, dict), res
    assert res["items"] == [{"id": track_id, "title": "Seeded Track"}]
    assert res["result_set_id"]
    assert res["receipt"]["capability_key"] == "integral_query_spec"
    assert res["_receipt"] == res["receipt"]


@pytest.mark.asyncio
async def test_call_tool_unknown_is_error(bind_fresh_graph_context_for_async_tests):
    """An unknown tool -> ``CallToolResult(isError=True)``, not a dict."""
    auth_user_id, workspace_id, _track_id = await _bootstrap_principal_and_track()

    res = await _call_tool_impl(
        "integral_not_a_tool",
        {},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert isinstance(res, types.CallToolResult)
    assert res.isError is True
    text = " ".join(c.text for c in res.content if isinstance(c, types.TextContent))
    assert "unknown" in text.lower()


@pytest.mark.asyncio
async def test_call_tool_propose_stages(bind_fresh_graph_context_for_async_tests):
    """A propose tool STAGES a change and does NOT apply it.

    Dispatching ``integral_create_entry`` returns a staged-change dict (token +
    ``_kind == "staged_change"``); the seeded track's entry count is unchanged
    afterward, proving the write was staged rather than applied.
    """
    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()

    # Count entries before via the read tool.
    before = await _call_tool_impl(
        "integral_query_entries",
        {"track_id": track_id},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert isinstance(before, dict), before
    n_before = len(before.get("entries", []))

    res = await _call_tool_impl(
        "integral_create_entry",
        {"track_id": track_id, "title": "Drafted via MCP", "text": "Q4 planning"},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    # Propose returns a staged-change dict (structured content), not an error.
    assert isinstance(res, dict), res
    assert res.get("_kind") == "staged_change", res
    assert res.get("token"), res
    assert res.get("state") == "pending", res

    # The token is real: a pending StagedChange exists for the principal.
    from app.agentive.staging import get_token

    sc = await get_token(res["token"])
    assert sc is not None
    assert sc.user_id == auth_user_id  # identity from dispatch, not args

    # Staged-not-applied: entry count is unchanged.
    after = await _call_tool_impl(
        "integral_query_entries",
        {"track_id": track_id},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert isinstance(after, dict), after
    n_after = len(after.get("entries", []))
    assert n_after == n_before, f"propose must not apply: {n_before} -> {n_after}"


@pytest.mark.asyncio
async def test_call_tool_dispatch_error_enveloped(
    bind_fresh_graph_context_for_async_tests,
):
    """A dispatch-level error (e.g. invalid propose) -> CallToolResult(isError).

    ``integral_author_profile`` with no description fails closed in the stager;
    the impl envelopes that ToolResult error as a ``CallToolResult`` rather than
    leaking a dict.
    """
    auth_user_id, workspace_id, _track_id = await _bootstrap_principal_and_track()

    res = await _call_tool_impl(
        "integral_author_profile",
        {},
        principal_id=auth_user_id,
        scope=workspace_id,
    )
    assert isinstance(res, types.CallToolResult)
    assert res.isError is True


@pytest.mark.asyncio
async def test_build_mcp_server_registers_handlers():
    """``build_mcp_server`` returns an MCPServer with list/call handlers wired."""
    from mcp.server.lowlevel.server import Server as MCPServer

    mcp = build_mcp_server()
    assert isinstance(mcp, MCPServer)
    assert mcp.name == "integral"
    assert mcp.instructions  # non-empty instructions string

    # The list/call request handlers are registered (no import-time transport).
    assert types.ListToolsRequest in mcp.request_handlers
    assert types.CallToolRequest in mcp.request_handlers


@pytest.mark.asyncio
async def test_build_session_manager_returns_manager():
    """``build_session_manager`` yields an (MCPServer, StreamableHTTPSessionManager)."""
    from mcp.server.lowlevel.server import Server as MCPServer
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    from app.agentive.mcp.server import build_session_manager

    mcp, mgr = build_session_manager()
    assert isinstance(mcp, MCPServer)
    assert isinstance(mgr, StreamableHTTPSessionManager)
