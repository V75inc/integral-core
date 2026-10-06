"""Real graph identity and transaction qualification for native approval."""

import uuid

import pytest
from jvspatial.core.context import GraphContext, set_default_context

from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.design_approval import authorize_pending_design_build
from app.api.errors import ResourceConflictError
from app.models.edges import IS_MEMBER_OF
from app.models.nodes import ChatThread
from app.services.chat_threads import append_message, create_thread
from app.services.harness_sessions import claim_harness_run, ensure_harness_session
from tests.fixtures.workspaces import make_org_workspace

pytestmark = pytest.mark.postgres


@pytest.fixture
def postgres_graph_context(postgres_raw_db):
    """Provide an isolated graph context backed by PostgreSQL."""
    from jvspatial.core.context import _default_context_var

    token = set_default_context(GraphContext(database=postgres_raw_db))
    try:
        yield
    finally:
        _default_context_var.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("replace_run_during_judgment", [False, True])
async def test_primary_build_selection_uses_graph_session_and_current_run(
    postgres_graph_context, replace_run_during_judgment
):
    """The selected build tool can approve only the current graph run."""
    from app.services.app_graph import catalog_workspace, ensure_integral_app_graph

    await ensure_integral_app_graph(include_library=False)
    workspace = await make_org_workspace(f"approval-{uuid.uuid4().hex}")
    await catalog_workspace(workspace)
    owners = await workspace.nodes(
        edge=[IS_MEMBER_OF], node=["User"], direction="in", limit=1
    )
    thread = await create_thread(
        user_id=owners[0].id,
        workspace_id=workspace.id,
        provider_id="integral_native",
    )
    await append_message(
        thread=thread,
        role="user",
        parts=[{"type": "text", "text": "Set up an equipment register."}],
    )
    thread.design_proposed = {
        "design_id": "design-a",
        "proposal": "Equipment register with serial number, condition and location.",
        "proposed_at": "2026-10-05T00:00:00+00:00",
        "proposed_at_user_turn": 1,
        "blueprint_digest": "digest-a",
        "blueprint_revision": 1,
    }
    await thread.save()
    utterance = "That sounds good. Go ahead."
    await append_message(
        thread=thread, role="user", parts=[{"type": "text", "text": utterance}]
    )
    scope = HarnessExecutionScope(
        tenant_id=workspace.id,
        workspace_id=workspace.id,
        principal_id=owners[0].id,
        thread_id=thread.id,
        session_id=str(uuid.uuid4()),
        run_id=str(uuid.uuid4()),
        permission_revision="permissions-a",
        capability_version="capabilities-a",
    )
    session = await ensure_harness_session(scope=scope, binding_id="integral_native")
    assert session.id != scope.session_id
    await claim_harness_run(scope=scope)

    if replace_run_during_judgment:
        await claim_harness_run(scope=scope.model_copy(update={"run_id": "newer-run"}))
        with pytest.raises(ResourceConflictError, match="no longer current"):
            await authorize_pending_design_build(
                scope=scope,
                expected_user_turn=2,
                expected_utterance=utterance,
            )
        current = await ChatThread.get(thread.id)
        assert not current.design_proposed.get("approved")
    else:
        assert await authorize_pending_design_build(
            scope=scope,
            expected_user_turn=2,
            expected_utterance=utterance,
        )
        current = await ChatThread.get(thread.id)
        assert current.active_harness_session_id == session.id
        assert current.design_proposed["approved"] is True
        assert current.design_proposed["affirm_run_id"] == scope.run_id
        assert current.design_proposed["blueprint_digest"] == "digest-a"
