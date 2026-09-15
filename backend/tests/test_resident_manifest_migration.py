"""Post-cutover tests for the resident manifest-migration (Task 6).

``EmbeddedIntegralAction.get_tools()`` is being rewritten to GENERATE the
jvagent ``Tool`` list from the shared manifest catalogue
(``app.agentive.tooling.build_tool_catalogue``), each tool's ``execute``
adapting to the single ``dispatch_tool`` seam. The bespoke
``build_integral_tools`` closures (and the per-op action methods they called)
are deleted. This file is the net for that cutover:

* **Parity** — ``get_tools()`` advertises EXACTLY the catalogue's tool names
  (PC-9: the resident surface == the closed manifest catalogue, no bespoke
  tool escaping the manifest).
* **Read threading** — a read tool dispatched under a bound
  ``ToolDispatchContext(user_id=...)`` + ``current_scope_workspace_id`` round-
  trips the seeded track, proving identity (PC-1) + scope (PC-2) thread from
  the dispatch context (NEVER from tool args) into the substrate read.
* **Propose staging** — a propose tool returns a pending ``staged_change``
  WITHOUT applying (PC-8: proposes stage, never apply); the bless path is what
  later commits.

The resident tools live in the jvagent app-dir
(``agent/agents/integral/integral_agent/actions/integral/
embedded_integral_action/``), not under ``backend/``. We reuse the
``test_resident_tools_smoke`` ``sys.path`` shim (its module add runs at import
time when pytest collects that file alongside this one) AND replicate the same
shim locally so this file is import-order-independent. Identity + scope are
bound exactly as the runtime does it: a frozen ``ToolDispatchContext`` on
``_dispatch_context_var`` and the bootstrapped workspace id on
``current_scope_workspace_id``.
"""

from __future__ import annotations

import contextlib
import os
import sys

import pytest

# --------------------------------------------------------------------------- #
# Make the resident embedded-action package importable (mirrors the smoke
# test's shim — see its module docstring for why we add the PARENT dir).
# --------------------------------------------------------------------------- #
_AGENT_ACTIONS_DIR = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "agent",
        "agents",
        "integral",
        "integral_agent",
        "actions",
        "integral",
    )
)
if _AGENT_ACTIONS_DIR not in sys.path:
    sys.path.insert(0, _AGENT_ACTIONS_DIR)


@contextlib.contextmanager
def _bound_identity_and_scope(user_id: str, workspace_id):
    """Bind the resident's dispatch-context user_id + agent scope workspace id.

    Mirrors the runtime contract (and the smoke test's helper): the orchestrator
    binds a ``ToolDispatchContext`` (carrying ``user_id``) around the tool loop
    and the chat router publishes the active workspace on
    ``current_scope_workspace_id``. We set both directly and reset them on exit
    so calls cannot leak across tests.
    """
    from jvagent.tooling.tool_executor import ToolDispatchContext, _dispatch_context_var

    from app.services.agent_scope import current_scope_workspace_id

    ctx_token = _dispatch_context_var.set(
        ToolDispatchContext(user_id=user_id, session_id="migration-session")
    )
    scope_token = current_scope_workspace_id.set(workspace_id)
    try:
        yield
    finally:
        current_scope_workspace_id.reset(scope_token)
        _dispatch_context_var.reset(ctx_token)


async def _bootstrap_principal_and_track():
    """Create an AuthUser + User + personal workspace, then a track in it.

    Returns ``(auth_user_id, workspace_id, track_id)``. Mirrors
    ``tests/test_tooling_dispatch_read._bootstrap_principal_and_track``
    (no HTTP signup; real route handlers seed the graph).
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
        UserCreate(email="resident-migration@example.com", password="testpassword123")
    )
    auth_user_id = user_response.id
    user_node = await User.create(
        user_id=auth_user_id, display_name="Resident Migration"
    )
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


def _tool(tools, name):
    """Return the resident Tool with ``name``; fail loudly if absent."""
    for t in tools:
        if t.name == name:
            return t
    raise AssertionError(f"tool {name!r} not in surface: {[t.name for t in tools]}")


async def _exec(tools, name, **args):
    """Execute a resident tool closure by name, awaiting the raw dict result."""
    import inspect

    result = _tool(tools, name).execute(**args)
    if inspect.isawaitable(result):
        result = await result
    return result


@pytest.fixture(autouse=True)
def _reset_staging_store():
    """Clear the module-global in-memory staging store around each test."""
    from app.agentive.staging import _reset_for_tests

    _reset_for_tests()
    yield
    _reset_for_tests()


# --------------------------------------------------------------------------- #
# Parity — get_tools() advertises exactly the manifest catalogue (PC-9).
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_get_tools_generates_full_catalogue():
    """get_tools() names == build_tool_catalogue() names, exactly (PC-9)."""
    from embedded_integral_action import EmbeddedIntegralAction

    from app.agentive.tooling import build_tool_catalogue

    tools = await EmbeddedIntegralAction().get_tools()
    names = {t.name for t in tools}
    catalogue_names = {t["name"] for t in build_tool_catalogue()}

    assert names == catalogue_names, names ^ catalogue_names
    # Canary names that must survive the flat-naming cutover.
    assert "integral_create_entry" in names
    assert "integral_whoami" in names
    # Every emitted tool carries its catalogue description + input schema.
    by_name = {t["name"]: t for t in build_tool_catalogue()}
    for t in tools:
        spec = by_name[t.name]
        assert t.description == spec["description"], t.name
        assert t.parameters_schema == spec["input_schema"], t.name


# --------------------------------------------------------------------------- #
# Read dispatch — a read tool round-trips the seeded track under ctx + scope.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_resident_read_dispatches_through_manifest(
    bind_fresh_graph_context_for_async_tests,
):
    """integral_list_tracks dispatches through the manifest path under ctx+scope.

    Proves the adapter threads identity (PC-1, from the dispatch context's
    ``user_id``, never a tool arg) and scope (PC-2, from
    ``current_scope_workspace_id``) into ``dispatch_tool`` so the substrate read
    returns the bootstrapped user's seeded track.
    """
    from embedded_integral_action import EmbeddedIntegralAction

    auth_user_id, workspace_id, _track_id = await _bootstrap_principal_and_track()
    tools = await EmbeddedIntegralAction().get_tools()

    with _bound_identity_and_scope(auth_user_id, workspace_id):
        result = await _exec(tools, "integral_list_tracks")

    assert isinstance(result, dict), result
    assert not result.get("error"), result
    titles = [t.get("title") for t in result.get("tracks", [])]
    assert "Seeded Track" in titles, result


# --------------------------------------------------------------------------- #
# Propose staging — a propose tool stages WITHOUT applying (PC-8).
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_resident_propose_stages_without_applying(
    bind_fresh_graph_context_for_async_tests,
):
    """integral_create_entry returns a pending staged_change and creates NO entry.

    Behavioral mapping (prepare+execute -> propose+bless): the bespoke surface
    split create into ``prepare_create_entry`` (stage) + ``execute_create_entry``
    (commit behind a blessed token). The manifest surface collapses that to a
    single ``propose``-classed ``integral_create_entry`` that STAGES only — the
    chat bless endpoint applies later via the staging executors. Here we assert
    the staged-change envelope shape and that nothing was written.
    """
    from embedded_integral_action import EmbeddedIntegralAction

    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()
    tools = await EmbeddedIntegralAction().get_tools()

    with _bound_identity_and_scope(auth_user_id, workspace_id):
        staged = await _exec(
            tools,
            "integral_create_entry",
            text="A staged note that must not be written until blessed",
            track_id=track_id,
            focused_track_id=track_id,
        )
        assert isinstance(staged, dict), staged
        assert not staged.get("error"), staged
        assert staged.get("_kind") == "staged_change", staged
        assert staged.get("state") == "pending", staged
        assert staged.get("token"), staged

        # Not applied yet — the track entry list is unchanged.
        listed = await _exec(tools, "integral_query_entries", track_id=track_id)
        assert isinstance(listed, dict), listed
        assert not listed.get("error"), listed
        entries = listed.get("entries", [])
        assert entries == [] or all(
            "staged note" not in (e.get("title") or "").lower() for e in entries
        ), listed
