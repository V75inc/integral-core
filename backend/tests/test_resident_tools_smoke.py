"""Behavioral smoke tests for the RESIDENT embedded-action tool surface.

Post-cutover regression gate (manifest-migration Task 6). The resident
``EmbeddedIntegralAction.get_tools()`` no longer builds a bespoke
prepare/execute tool taxonomy (the retired ``build_integral_tools`` closures);
it GENERATES the surface from the shared manifest catalogue
(``app.agentive.tooling.build_tool_catalogue``) and routes every call through
the single ``dispatch_tool`` seam — the same path the per-user MCP server uses.

Behavioral mapping across the cutover
-------------------------------------

* tool NAMES flattened: ``integral_<area>__<op>`` -> flat ``integral_<op>``
  (e.g. ``integral_workspace__list_tracks`` -> ``integral_list_tracks``,
  ``integral_identity__whoami`` -> ``integral_whoami``,
  ``integral_entries__get_entry`` -> ``integral_resolve_entry``,
  ``integral_entries__list_entries`` -> ``integral_query_entries``).
* the prepare/execute SPLIT collapsed to propose-only: the bespoke
  ``prepare_create_entry`` (stage) + ``execute_create_entry`` (commit behind a
  blessed token) become a single ``propose``-classed ``integral_create_entry``
  that STAGES; the chat bless endpoint applies later via the staging executors.
  We exercise that real apply path here (bless -> staging_executors.dispatch ->
  consume) so the end-to-end stage→approve→commit behavior stays covered.
* the error envelope shape is preserved: a read with a bad id returns
  ``{error: True, error_code, message}`` (no raised exception, no ToolResult).

The READ round-trip assertions are name-stable across the cutover and stay
green; the prepare/execute-cycle assertions are replaced by the propose+bless
equivalents below.

The resident tools live in the jvagent app-dir
(``agent/agents/integral/integral_agent/actions/integral/
embedded_integral_action/``), not under ``backend/``. We add that package's
PARENT dir to ``sys.path`` (the package is named ``embedded_integral_action``;
adding the dir itself would shadow the same-named ``.py`` module inside it,
so we add the ``…/integral`` parent) and import it as a normal package. The
import resolves against the backend ``app.*`` modules the resident already
depends on, so the tools run the exact substrate code path the read/propose
dispatch tests exercise.

Identity + scope synthesis
--------------------------

Resident tool closures read identity from the jvagent dispatch-context
ContextVar (``get_dispatch_context()`` →
``jvagent.tooling.tool_executor._dispatch_context_var``) and workspace scope
from ``app.services.agent_scope.current_scope_workspace_id``. We bind both
directly in a context manager:

* a frozen ``ToolDispatchContext(user_id=...)`` set on
  ``_dispatch_context_var`` (no live walker/visitor to synthesize), and
* the bootstrapped workspace id set on ``current_scope_workspace_id``.

This is the same pair the resident reads at runtime — the chat router binds
the dispatch context around the orchestrator loop and sets the scope from the
frontend ``X-Integral-Scope`` header.
"""

from __future__ import annotations

import contextlib
import os
import sys

import pytest

# --------------------------------------------------------------------------- #
# Make the resident embedded-action package importable.
# Package dir: …/integral/embedded_integral_action  (contains __init__.py).
# We add its PARENT (…/integral) so ``import embedded_integral_action`` finds
# the package, not the same-named ``embedded_integral_action.py`` inside it.
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

    Mirrors the runtime contract: the orchestrator binds a
    ``ToolDispatchContext`` (carrying ``user_id``) around the tool loop and the
    chat router publishes the active workspace on ``current_scope_workspace_id``.
    We set both directly and reset them on exit so calls cannot leak across
    tests.
    """
    from jvagent.tooling.tool_executor import ToolDispatchContext, _dispatch_context_var

    from app.services.agent_scope import current_scope_workspace_id

    ctx_token = _dispatch_context_var.set(
        ToolDispatchContext(user_id=user_id, session_id="smoke-session")
    )
    scope_token = current_scope_workspace_id.set(workspace_id)
    try:
        yield
    finally:
        current_scope_workspace_id.reset(scope_token)
        _dispatch_context_var.reset(ctx_token)


def _tool(tools, name):
    """Return the resident Tool with ``name``; fail loudly if absent."""
    for t in tools:
        if t.name == name:
            return t
    raise AssertionError(f"tool {name!r} not in surface: {[t.name for t in tools]}")


async def _exec(tools, name, **args):
    """Execute a resident tool closure by name, awaiting the raw dict result.

    Calls ``Tool.execute`` (the resident closure) rather than ``Tool.call`` so
    we observe the resident's own return shape (plain dicts / error envelopes),
    not the jvagent ``ToolResult`` wrapper.
    """
    import inspect

    result = _tool(tools, name).execute(**args)
    if inspect.isawaitable(result):
        result = await result
    return result


async def _bless_and_apply(user_id: str, token: str):
    """Replicate the chat bless endpoint's apply path for a staged token.

    The cutover collapsed prepare/execute into a single ``propose`` op; the
    write now runs only when the user blesses the token. The chat surface
    (``app.agentive.api.staging``) does this in three steps — bless, run the
    matching staging executor, consume — which we replay here so the smoke
    test still exercises an end-to-end commit behind a blessed token.
    """
    from app.agentive.staging import bless_token, consume_token
    from app.agentive.staging_executors import dispatch as _dispatch_kind

    sc = await bless_token(user_id=user_id, token=token)
    exec_result = await _dispatch_kind(
        user_id=user_id, kind=sc.kind, payload=sc.payload
    )
    assert not exec_result.get("error"), exec_result
    await consume_token(user_id=user_id, token=token, expected_kind=sc.kind)
    return exec_result


async def _bootstrap_principal_and_track():
    """Create an AuthUser + User + personal workspace, then a track in it.

    Returns ``(auth_user_id, workspace_id, track_id, display_name, email)``.
    Mirrors ``tests/test_tooling_dispatch_read._bootstrap_principal_and_track``
    (no HTTP signup; real route handlers seed the graph).
    """
    from jvspatial.api.auth.models import UserCreate

    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.auth import _get_auth_service
    from app.api.tracks import create_track
    from app.models.nodes import User
    from app.services.app_graph import catalog_user
    from app.services.personal_workspace import ensure_personal_workspace

    email = "resident-smoke@example.com"
    display_name = "Resident Smoke"
    auth_service = _get_auth_service()
    user_response = await auth_service.register_user(
        UserCreate(email=email, password="testpassword123")
    )
    auth_user_id = user_response.id
    user_node = await User.create(user_id=auth_user_id, display_name=display_name)
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
    return auth_user_id, workspace_id, track_id, display_name, email


@pytest.fixture(autouse=True)
def _reset_staging_store():
    """Clear the module-global in-memory staging store around each test."""
    from app.agentive.staging import _reset_for_tests

    _reset_for_tests()
    yield
    _reset_for_tests()


@pytest.fixture
async def resident_tools():
    """Build the resident tool surface once per test (manifest-generated)."""
    from embedded_integral_action import EmbeddedIntegralAction

    return await EmbeddedIntegralAction().get_tools()


# --------------------------------------------------------------------------- #
# whoami — identity round-trips from the dispatch context.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_whoami_returns_bootstrapped_user(
    bind_fresh_graph_context_for_async_tests, resident_tools
):
    """integral_whoami returns the bootstrapped user's profile."""
    auth_user_id, workspace_id, _track_id, display_name, email = (
        await _bootstrap_principal_and_track()
    )

    with _bound_identity_and_scope(auth_user_id, workspace_id):
        result = await _exec(resident_tools, "integral_whoami")

    assert isinstance(result, dict), result
    assert not result.get("error"), result
    # GET /api/auth/me shape — surfaces the identity we bound on the context.
    blob = " ".join(str(v) for v in result.values())
    assert email in blob or display_name in blob, result


# --------------------------------------------------------------------------- #
# Read tool — list_tracks round-trips under bootstrapped principal + scope.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_list_tracks_round_trips_seeded_track(
    bind_fresh_graph_context_for_async_tests, resident_tools
):
    """integral_list_tracks returns the seeded track (non-error)."""
    auth_user_id, workspace_id, _track_id, _name, _email = (
        await _bootstrap_principal_and_track()
    )

    with _bound_identity_and_scope(auth_user_id, workspace_id):
        result = await _exec(resident_tools, "integral_list_tracks")

    assert isinstance(result, dict), result
    assert not result.get("error"), result
    titles = [t.get("title") for t in result.get("tracks", [])]
    assert "Seeded Track" in titles, result


# --------------------------------------------------------------------------- #
# Propose → bless → apply: stage stays unapplied; bless+apply commits.
# (Behavioral mapping of the retired prepare/execute cycle — see module docs.)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_propose_create_entry_stages_without_applying(
    bind_fresh_graph_context_for_async_tests, resident_tools
):
    """integral_create_entry returns a pending staged_change and creates NO entry.

    Proves the resident propose path (replacing the bespoke prepare op):
    ``_kind == "staged_change"`` + a token + ``state == "pending"``, and the
    track's entry list is unchanged (the write runs only on a later bless).
    """
    auth_user_id, workspace_id, track_id, _name, _email = (
        await _bootstrap_principal_and_track()
    )

    with _bound_identity_and_scope(auth_user_id, workspace_id):
        staged = await _exec(
            resident_tools,
            "integral_create_entry",
            track_id=track_id,
            title="Staged Entry",
            body="from the smoke test",
        )
        assert isinstance(staged, dict), staged
        assert not staged.get("error"), staged
        assert staged.get("_kind") == "staged_change", staged
        assert staged.get("state") == "pending", staged
        assert staged.get("token"), staged

        # Not applied yet — the entry is absent from the track.
        listed = await _exec(
            resident_tools,
            "integral_query_entries",
            track_id=track_id,
        )
        before_titles = [e.get("title") for e in listed.get("entries", [])]
        assert "Staged Entry" not in before_titles, listed


@pytest.mark.asyncio
async def test_bless_create_entry_applies_staged_token(
    bind_fresh_graph_context_for_async_tests, resident_tools
):
    """Blessing the staged token APPLIES the create — the entry appears.

    Runs the full propose → bless → apply cycle end-to-end: the resident
    ``integral_create_entry`` propose mints a token, the user blesses it
    (the chat surface then runs the matching staging executor and consumes the
    token), and the entry is present in the track listing afterward. This is the
    behavioral successor to the retired ``execute_create_entry`` path.
    """
    auth_user_id, workspace_id, track_id, _name, _email = (
        await _bootstrap_principal_and_track()
    )

    with _bound_identity_and_scope(auth_user_id, workspace_id):
        staged = await _exec(
            resident_tools,
            "integral_create_entry",
            track_id=track_id,
            title="Applied Entry",
            body="committed via bless",
        )
        token = staged["token"]

        # User approves the staged change via the chat surface (bless + apply).
        await _bless_and_apply(auth_user_id, token)

        # Applied — the entry is now present in the track.
        listed = await _exec(
            resident_tools,
            "integral_query_entries",
            track_id=track_id,
        )
        after_titles = [e.get("title") for e in listed.get("entries", [])]
        assert "Applied Entry" in after_titles, listed


# --------------------------------------------------------------------------- #
# Error path — a read with a bad id returns the resident {error:True,...} shape.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_resolve_entry_bad_id_returns_resident_error_envelope(
    bind_fresh_graph_context_for_async_tests, resident_tools
):
    """A read tool with a non-existent id returns the resident error envelope.

    The handler raises a typed JVSpatialAPIException for a missing resource; the
    dispatch path envelopes it into a structured ``{error: True, error_code,
    message}`` dict (NOT a raised exception, NOT a ToolResult). This pins the
    exact envelope shape the cutover must preserve.
    """
    auth_user_id, workspace_id, _track_id, _name, _email = (
        await _bootstrap_principal_and_track()
    )

    with _bound_identity_and_scope(auth_user_id, workspace_id):
        result = await _exec(
            resident_tools,
            "integral_resolve_entry",
            entry_id="n.Entry.does-not-exist",
        )

    assert isinstance(result, dict), result
    assert result.get("error") is True, result
    assert result.get("error_code"), result
    assert "message" in result, result
