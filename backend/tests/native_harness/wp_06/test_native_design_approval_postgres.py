"""Real graph identity and transaction qualification for native approval."""

import uuid

import pytest
from jvspatial.api.auth.models import User as AuthUser
from jvspatial.core.context import GraphContext, set_default_context

from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.design_approval import authorize_pending_design_build
from app.api.errors import ResourceConflictError
from app.models.edges import IS_MEMBER_OF
from app.models.nodes import ChatThread, User
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


async def _pending_design(already_approved=False):
    """Create a native thread with real current identity and an exact design."""
    from app.services.app_graph import (
        catalog_user,
        catalog_workspace,
        ensure_integral_app_graph,
    )

    await ensure_integral_app_graph(include_library=False)
    suffix = uuid.uuid4().hex
    auth = await AuthUser.create(
        email=f"design-{suffix}@example.com", password_hash="fixture"
    )
    profile = await User.create(user_id=auth.id, display_name="Design owner")
    await catalog_user(profile)
    workspace = await make_org_workspace(f"approval-{suffix}", owner=profile)
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
        "approved": already_approved,
        "affirm_run_id": "earlier-run" if already_approved else "",
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

    return scope, thread, auth, workspace, utterance, session


@pytest.mark.asyncio
@pytest.mark.parametrize("replace_run_during_judgment", [False, True])
@pytest.mark.parametrize("already_approved", [False, True])
async def test_primary_build_selection_uses_graph_session_and_current_run(
    postgres_graph_context, replace_run_during_judgment, already_approved
):
    """The selected build tool can approve only the current graph run."""
    scope, thread, _, _, utterance, session = await _pending_design(already_approved)
    if replace_run_during_judgment:
        await claim_harness_run(scope=scope.model_copy(update={"run_id": "newer-run"}))
        with pytest.raises(ResourceConflictError, match="no longer current"):
            await authorize_pending_design_build(
                scope=scope,
                expected_user_turn=2,
                expected_utterance=utterance,
            )
        current = await ChatThread.get(thread.id)
        assert bool(current.design_proposed.get("approved")) is already_approved
        assert current.design_proposed["affirm_run_id"] != scope.run_id
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
        accepted = dict(current.design_proposed)
        assert await authorize_pending_design_build(
            scope=scope, expected_user_turn=2, expected_utterance=utterance
        )
        assert (await ChatThread.get(thread.id)).design_proposed == accepted


@pytest.mark.asyncio
@pytest.mark.parametrize("already_approved", [False, True])
async def test_revoked_workspace_cannot_accept_or_resume_design(
    postgres_graph_context, already_approved
):
    """Saved thread/run ownership never substitutes for current workspace access."""
    from app.api.errors import InsufficientPermissionsError
    from tests.contract.test_release_admission_postgres import remove_membership

    scope, thread, auth, workspace, utterance, _ = await _pending_design(
        already_approved
    )
    before = dict(thread.design_proposed)
    await remove_membership(auth, workspace)
    with pytest.raises(InsufficientPermissionsError):
        await authorize_pending_design_build(
            scope=scope, expected_user_turn=2, expected_utterance=utterance
        )
    assert (await ChatThread.get(thread.id)).design_proposed == before


@pytest.mark.asyncio
@pytest.mark.parametrize("already_approved", [False, True])
async def test_detached_session_cannot_authorize_design(
    postgres_graph_context, already_approved
):
    """A scalar active-session pointer cannot replace its structural edge."""
    from app.models.edges import HAS_HARNESS_SESSION

    scope, thread, _, _, utterance, session = await _pending_design(already_approved)
    before = dict(thread.design_proposed)
    context = await thread.get_context()
    links = await context.find_edges_between(
        thread.id, session.id, edge_class=HAS_HARNESS_SESSION
    )
    assert len(links) == 1
    await links[0].delete()
    with pytest.raises(ResourceConflictError, match="detached or ambiguous"):
        await authorize_pending_design_build(
            scope=scope, expected_user_turn=2, expected_utterance=utterance
        )
    assert (await ChatThread.get(thread.id)).design_proposed == before


@pytest.mark.asyncio
@pytest.mark.parametrize("already_approved", [False, True])
async def test_inactive_identity_cannot_authorize_design(
    postgres_graph_context, already_approved
):
    """Earlier approval cannot authorize a disabled account's continuation."""
    from app.api.errors import InsufficientPermissionsError
    from app.services.membership_write_scope import membership_write_scope

    scope, thread, auth, workspace, utterance, _ = await _pending_design(
        already_approved
    )
    before = dict(thread.design_proposed)
    async with membership_write_scope(workspace.id, principal_ids=(auth.id,)):
        from app.services.app_operations.transaction_scope import (
            current_graph_transaction_database,
        )

        await current_graph_transaction_database().find_one_and_update(
            "object", {"id": auth.id}, {"$set": {"context.is_active": False}}
        )
    with pytest.raises(InsufficientPermissionsError):
        await authorize_pending_design_build(
            scope=scope, expected_user_turn=2, expected_utterance=utterance
        )
    assert (await ChatThread.get(thread.id)).design_proposed == before


@pytest.mark.asyncio
@pytest.mark.parametrize("already_approved", [False, True])
async def test_revised_design_cannot_be_overwritten_by_inflight_selection(
    postgres_graph_context, monkeypatch, already_approved
):
    """A newly persisted revision survives an older run's approval attempt."""
    from contextlib import asynccontextmanager

    from app.agentive.harness import design_approval

    scope, thread, _, _, utterance, _ = await _pending_design(already_approved)
    transaction = design_approval._authority_transaction
    revised = dict(thread.design_proposed)
    revised.update(approved=False, blueprint_revision=2, blueprint_digest="digest-b")

    @asynccontextmanager
    async def revise_before_admission(work_context):
        current = await ChatThread.get(thread.id)
        current.design_proposed = revised
        await current.save()
        async with transaction(work_context) as database:
            yield database

    monkeypatch.setattr(
        design_approval, "_authority_transaction", revise_before_admission
    )
    with pytest.raises(ResourceConflictError, match="changed during approval"):
        await authorize_pending_design_build(
            scope=scope, expected_user_turn=2, expected_utterance=utterance
        )
    assert (await ChatThread.get(thread.id)).design_proposed == revised


@pytest.mark.asyncio
async def test_design_commit_orders_with_revocation_and_later_retry_is_denied(
    postgres_graph_context, monkeypatch
):
    """Revocation waits for an admitted approval and bars its next continuation."""
    import asyncio

    from app.agentive.harness import design_approval
    from app.api.errors import InsufficientPermissionsError
    from tests.contract.test_release_admission_postgres import remove_membership

    scope, thread, auth, workspace, utterance, session = await _pending_design(True)
    entered, unblock = asyncio.Event(), asyncio.Event()
    get_session = design_approval.HarnessSession.get

    async def pause_inside_admission(session_id):
        value = await get_session(session_id)
        if session_id == session.id:
            entered.set()
            await unblock.wait()
        return value

    monkeypatch.setattr(design_approval.HarnessSession, "get", pause_inside_admission)
    approving = asyncio.create_task(
        authorize_pending_design_build(
            scope=scope, expected_user_turn=2, expected_utterance=utterance
        )
    )
    revoking = None
    try:
        await asyncio.wait_for(entered.wait(), 10)
        revoking = asyncio.create_task(remove_membership(auth, workspace))
        await asyncio.sleep(0.05)
        assert not revoking.done()
        unblock.set()
        assert await asyncio.wait_for(approving, 10)
        await asyncio.wait_for(revoking, 10)
        accepted = dict((await ChatThread.get(thread.id)).design_proposed)
        with pytest.raises(InsufficientPermissionsError):
            await authorize_pending_design_build(
                scope=scope, expected_user_turn=2, expected_utterance=utterance
            )
        assert (await ChatThread.get(thread.id)).design_proposed == accepted
    finally:
        unblock.set()
        await asyncio.gather(
            approving,
            *([revoking] if revoking is not None else []),
            return_exceptions=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("already_approved", [False, True])
async def test_persisted_legacy_card_remains_unapplied_without_rewriting_design(
    postgres_graph_context, already_approved
):
    """The real approval pipeline records a refused legacy card without effects."""
    from app.agentive.services.staging_apply import bless_and_execute
    from app.agentive.staging import create_staged_change, get_token
    from app.services.agent_scope import current_scope_workspace_id

    scope, thread, auth, workspace, _, _ = await _pending_design(already_approved)
    before = dict(thread.design_proposed)
    reset = current_scope_workspace_id.set(workspace.id)
    try:
        change = await create_staged_change(
            user_id=auth.id,
            session_id=thread.id,
            kind="design_proposal",
            summary="Earlier outline approval",
            diff_human="Approve the earlier outline",
            diff_machine={},
            payload={
                "session_id": thread.id,
                "proposal": "Unreviewed replacement",
                "summary": "Changed outline",
            },
        )
    finally:
        current_scope_workspace_id.reset(reset)
    result = await bless_and_execute(user_id=auth.id, token=change.token)
    assert result["consumed"] is False, result
    assert (
        result["execute_result"]["error_code"]
        == "design_approval_reconciliation_required"
    )
    assert (await ChatThread.get(thread.id)).design_proposed == before
    retained = await get_token(change.token)
    assert retained is not None
    assert retained.state != "consumed"
    assert retained.last_error
