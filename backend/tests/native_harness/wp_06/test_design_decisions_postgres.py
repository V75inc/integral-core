"""Saved native designs remain visible and safely discardable across reload."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app.agentive.services.execution_runs import RunStep
from app.agentive.staging import create_staged_change, get_token
from app.api.errors import InsufficientPermissionsError, ResourceConflictError
from app.models.nodes import ChatThread
from app.services import chat_threads, design_decisions
from tests.native_harness.wp_06 import (
    test_native_design_approval_postgres as design_fixture,
)

_pending_design = design_fixture._pending_design
postgres_graph_context = design_fixture.postgres_graph_context

pytestmark = pytest.mark.postgres


async def discard(scope, marker):
    """Verify discard."""
    return await design_decisions.discard_design(
        user_id=scope.principal_id,
        workspace_id=scope.workspace_id,
        thread_id=scope.thread_id,
        design_id=marker["design_id"],
        blueprint_revision=marker["blueprint_revision"],
        proposed_at=marker["proposed_at"],
    )


@pytest.mark.asyncio
async def test_marker_without_token_can_be_found_and_discarded_after_reload(
    postgres_graph_context, monkeypatch
):
    """Verify marker without token can be found and discarded after reload."""
    scope, thread, _, _, _, _ = await _pending_design()
    marker = deepcopy(thread.design_proposed)
    rows = await design_decisions.list_design_decisions(
        user_id=scope.principal_id, workspace_id=scope.workspace_id
    )
    assert [row["thread_id"] for row in rows] == [thread.id]
    await discard(scope, marker)
    restored = await ChatThread.get(thread.id)
    assert restored.design_proposed["discarded_at"]
    assert not await design_decisions.list_design_decisions(
        user_id=scope.principal_id, workspace_id=scope.workspace_id
    )
    assert not await chat_threads.design_proposed_pending(thread.id)
    # The retained tombstone prevents implicit recovery from old chat prose.
    judge = AsyncMock(
        side_effect=AssertionError("discarded design must not be resurrected")
    )
    monkeypatch.setattr(chat_threads, "looks_like_design_affirm", judge)
    assert (
        await chat_threads.recover_visible_design_for_affirmed_build(
            user_id=scope.principal_id, session_id=thread.id, summary="old"
        )
        is None
    )
    judge.assert_not_awaited()


@pytest.mark.asyncio
async def test_discard_retires_legacy_design_token_and_queue(postgres_graph_context):
    """Verify discard retires legacy design token and queue."""
    scope, thread, _, _, _, _ = await _pending_design()
    marker = deepcopy(thread.design_proposed)
    token = await create_staged_change(
        user_id=scope.principal_id,
        workspace_id=scope.workspace_id,
        session_id=thread.id,
        kind="design_proposal",
        summary="Old card",
        diff_human="Outline",
        diff_machine={},
        payload={},
    )
    thread.prompt_queue = {
        "status": "open",
        "items": [
            {
                "kind": "staged_write",
                "write_kind": "design_proposal",
                "status": "pending",
                "token": token.token,
            }
        ],
    }
    await thread.save()
    await discard(scope, marker)
    assert (await get_token(token.token)).state == "revoked"
    restored = await ChatThread.get(thread.id)
    assert restored.prompt_queue["status"] == "closed"
    assert restored.prompt_queue["items"][0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_changed_revision_and_partial_effects_refuse_discard(
    postgres_graph_context,
):
    """Verify changed revision and partial effects refuse discard."""
    scope, thread, _, _, _, _ = await _pending_design()
    marker = deepcopy(thread.design_proposed)
    thread.design_proposed["blueprint_revision"] = 2
    await thread.save()
    with pytest.raises(ResourceConflictError):
        await discard(scope, marker)
    marker = deepcopy(thread.design_proposed)
    thread.design_proposed["partial_build"] = {"batch_token": "partial"}
    await thread.save()
    with pytest.raises(ResourceConflictError):
        await discard(scope, marker)
    assert (await ChatThread.get(thread.id)).design_proposed.get("partial_build")


@pytest.mark.asyncio
async def test_revoked_workspace_cannot_read_or_discard(postgres_graph_context):
    """Verify revoked workspace cannot read or discard."""
    from tests.contract.test_release_admission_postgres import remove_membership

    scope, thread, auth, workspace, _, _ = await _pending_design()
    marker = deepcopy(thread.design_proposed)
    await remove_membership(auth, workspace)
    with pytest.raises(InsufficientPermissionsError):
        await design_decisions.list_design_decisions(
            user_id=scope.principal_id, workspace_id=scope.workspace_id
        )
    with pytest.raises(InsufficientPermissionsError):
        await discard(scope, marker)
    assert (await ChatThread.get(thread.id)).design_proposed == marker


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "receipt_status,error,allowed",
    [
        (None, None, False),
        ("failed", "unknown", False),
        ("succeeded", None, False),
        ("failed", "invalid_scaffold_plan", True),
        ("denied", None, True),
    ],
)
async def test_confirmed_design_discard_requires_original_no_effect_proof(
    postgres_graph_context, receipt_status, error, allowed
):
    """Verify confirmed design discard requires original no effect proof."""
    scope, thread, _, _, _, _ = await _pending_design(True)
    marker = deepcopy(thread.design_proposed)
    if receipt_status:
        await RunStep.create(
            run_id=marker["affirm_run_id"],
            principal_id=scope.principal_id,
            workspace_id=scope.workspace_id,
            capability_key="integral_build_approved_design",
            capability_source="core",
            status=receipt_status,
            adapter_error_code=error,
        )
    if allowed:
        await discard(scope, marker)
        assert (await ChatThread.get(thread.id)).design_proposed.get("discarded_at")
    else:
        with pytest.raises(ResourceConflictError):
            await discard(scope, marker)
        assert (await ChatThread.get(thread.id)).design_proposed == marker


@pytest.mark.asyncio
async def test_late_partial_receipt_cannot_be_erased_by_stale_read(
    postgres_graph_context, monkeypatch
):
    """Verify late partial receipt cannot be erased by stale read."""
    scope, thread, _, _, _, _ = await _pending_design()
    marker = deepcopy(thread.design_proposed)
    stale = thread.model_copy()
    stale.design_proposed = deepcopy(thread.design_proposed)
    thread.design_proposed["partial_build"] = {"batch_token": "late-partial"}
    await thread.save()
    original = ChatThread.get
    calls = 0

    async def get(identifier):
        """Verify get."""
        nonlocal calls
        calls += 1
        if calls == 1:
            return stale
        return await original(identifier)

    monkeypatch.setattr(ChatThread, "get", get)
    with pytest.raises(ResourceConflictError):
        await discard(scope, marker)
    assert (await original(thread.id)).design_proposed["partial_build"][
        "batch_token"
    ] == "late-partial"


@pytest.mark.asyncio
async def test_pending_native_work_is_preserved(postgres_graph_context):
    """Verify pending native work is preserved."""
    from app.agentive.work_models import WorkItem

    scope, thread, _, _, _, _ = await _pending_design()
    marker = deepcopy(thread.design_proposed)
    work = await WorkItem.create(
        id="o.WorkItem.design-pending",
        work_item_id="design-pending",
        principal_id=scope.principal_id,
        workspace_id=scope.workspace_id,
        thread_id=thread.id,
        status="waiting_for_human",
    )
    thread.active_work_item_id = work.work_item_id
    await thread.save()
    with pytest.raises(ResourceConflictError, match="unsettled work"):
        await discard(scope, marker)
    assert (await ChatThread.get(thread.id)).design_proposed == marker
    assert (await WorkItem.get(work.id)).status == "waiting_for_human"


@pytest.mark.asyncio
@pytest.mark.parametrize("mixed", [False, True])
async def test_unproven_or_mixed_draft_is_preserved(postgres_graph_context, mixed):
    """Fail closed without deleting unrelated or mixed staged resource work."""
    from app.agentive.staging import (
        append_operations_to_batch,
        cancel_batch,
        open_batch,
        peek_open_batch,
    )
    from app.agentive.staging_store import OpenBatchRecord, _open_batch_record_id

    scope, thread, _, _, _, _ = await _pending_design()
    marker = deepcopy(thread.design_proposed)
    await open_batch(
        user_id=scope.principal_id,
        session_id=thread.id,
        label="Unrelated resource edits",
    )
    operations = [
        {
            "kind": "update_entry",
            "payload": {"entry_id": "unrelated", "title": "Keep this edit"},
        }
    ]
    if mixed:
        operations.append(
            {
                "kind": "create_app",
                "payload": {
                    "name": "A scaffold-shaped operation is not ownership proof"
                },
            }
        )
    await append_operations_to_batch(
        user_id=scope.principal_id, session_id=thread.id, operations=operations
    )
    before = deepcopy(peek_open_batch(scope.principal_id, thread.id))
    durable_id = _open_batch_record_id(scope.principal_id, thread.id)
    # TESTING deliberately disables durable batch writes, so retain an actual
    # stored snapshot explicitly to qualify the restart path as well.
    await OpenBatchRecord.create(
        id=durable_id,
        user_id=scope.principal_id,
        session_id=thread.id,
        label="Unrelated resource edits",
        batch_id="retained-batch",
        ops=operations,
    )
    durable_before = (await OpenBatchRecord.get(durable_id)).model_dump()
    try:
        with pytest.raises(
            ResourceConflictError, match="association cannot be verified"
        ):
            await discard(scope, marker)
        assert peek_open_batch(scope.principal_id, thread.id) == before
        assert (await OpenBatchRecord.get(durable_id)).model_dump() == durable_before
        assert (await ChatThread.get(thread.id)).design_proposed == marker
    finally:
        await cancel_batch(user_id=scope.principal_id, session_id=thread.id)


@pytest.mark.asyncio
async def test_projection_failure_retry_never_cancels_new_batch_or_token(
    postgres_graph_context, monkeypatch
):
    """Atomic token closure survives projection failure and exact retry."""
    from app.agentive.staging import cancel_batch, open_batch, peek_open_batch

    scope, thread, _, _, _, _ = await _pending_design()
    marker = deepcopy(thread.design_proposed)
    token = await create_staged_change(
        user_id=scope.principal_id,
        workspace_id=scope.workspace_id,
        session_id=thread.id,
        kind="design_proposal",
        summary="Old card",
        diff_human="Outline",
        diff_machine={},
        payload={},
    )
    original = design_decisions._retire_design_projections

    async def fail_projection(*args: object):
        raise RuntimeError("Simulated postcommit response loss")

    monkeypatch.setattr(design_decisions, "_retire_design_projections", fail_projection)
    with pytest.raises(RuntimeError, match="postcommit"):
        await discard(scope, marker)
    assert (await get_token(token.token)).state == "revoked"
    current = await ChatThread.get(thread.id)
    assert current.design_proposed["retired_token_ids"] == [token.token]
    await open_batch(
        user_id=scope.principal_id, session_id=thread.id, label="Fresh admitted turn"
    )
    fresh = await create_staged_change(
        user_id=scope.principal_id,
        workspace_id=scope.workspace_id,
        session_id=thread.id,
        kind="design_proposal",
        summary="Fresh card",
        diff_human="New outline",
        diff_machine={},
        payload={},
    )
    before = deepcopy(peek_open_batch(scope.principal_id, thread.id))
    monkeypatch.setattr(design_decisions, "_retire_design_projections", original)
    try:
        assert (await discard(scope, marker))["discarded"] is True
        assert peek_open_batch(scope.principal_id, thread.id) == before
        assert (await get_token(fresh.token)).state == "pending"
        assert (await get_token(token.token)).state == "revoked"
    finally:
        await cancel_batch(user_id=scope.principal_id, session_id=thread.id)
