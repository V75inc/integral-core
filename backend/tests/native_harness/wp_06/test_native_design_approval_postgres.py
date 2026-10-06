"""Real graph identity and transaction qualification for native approval."""

import uuid

import pytest
from jvspatial.core.context import GraphContext, set_default_context
from pydantic_ai import CancellationToken
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.design_approval import resolve_pending_design_reply
from app.api.errors import ResourceConflictError
from app.models.edges import IS_MEMBER_OF
from app.models.nodes import ChatThread
from app.services.chat_threads import append_message, create_thread
from app.services.harness_sessions import claim_harness_run, ensure_harness_session
from tests.fixtures.workspaces import make_org_workspace

pytestmark = pytest.mark.postgres


@pytest.fixture
def postgres_graph_context(postgres_raw_db):
    from jvspatial.core.context import _default_context_var

    token = set_default_context(GraphContext(database=postgres_raw_db))
    try:
        yield
    finally:
        _default_context_var.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("replace_run_during_judgment", [False, True])
async def test_exact_proposal_approval_uses_graph_session_and_current_run(
    postgres_graph_context, replace_run_during_judgment
):
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

    async def reply(_messages, info):
        if replace_run_during_judgment:
            await claim_harness_run(
                scope=scope.model_copy(update={"run_id": "newer-run"})
            )
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {"response": "approve"})]
        )

    arguments = dict(
        scope=scope,
        utterance=utterance,
        model=FunctionModel(reply),
        cancellation=CancellationToken(),
    )
    if replace_run_during_judgment:
        with pytest.raises(ResourceConflictError, match="no longer current"):
            await resolve_pending_design_reply(**arguments)
        current = await ChatThread.get(thread.id)
        assert not current.design_proposed.get("approved")
    else:
        assert await resolve_pending_design_reply(**arguments)
        current = await ChatThread.get(thread.id)
        assert current.active_harness_session_id == session.id
        assert current.design_proposed["approved"] is True
        assert current.design_proposed["affirm_run_id"] == scope.run_id
        assert current.design_proposed["blueprint_digest"] == "digest-a"
