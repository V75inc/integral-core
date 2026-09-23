"""WorkExecutionContext identity and effect-boundary gates (Task 5)."""

from __future__ import annotations

import hashlib

import pytest

from app.agentive.services import work_execution, work_items
from app.schemas.agentive.work import WorkError, WorkExecutionContext


def test_deterministic_run_id_and_effect_key() -> None:
    wid = "abc123"
    run_a = work_execution.deterministic_run_id(work_item_id=wid, attempt=1)
    run_b = work_execution.deterministic_run_id(work_item_id=wid, attempt=1)
    run_c = work_execution.deterministic_run_id(work_item_id=wid, attempt=2)
    assert run_a == run_b
    assert run_a.startswith("workrun:")
    assert run_a == "workrun:" + hashlib.sha256(b"abc123:1").hexdigest()
    assert run_c != run_a

    step = work_execution.logical_step_key_for(kind="capability")
    assert step == "capability:0"
    ek = work_execution.effect_key(work_item_id=wid, logical_step_key=step)
    assert ek == hashlib.sha256(f"{wid}:{step}".encode()).hexdigest()
    # Effect key independent of attempt.
    assert ek == work_execution.effect_key(work_item_id=wid, logical_step_key=step)


@pytest.mark.asyncio
async def test_build_context_from_claimed_work_item() -> None:
    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="wec-u-1",
        workspace_id="wec-ws-1",
        idempotency_key="wec-1",
        input_payload={"capability_key": "integral_list_entries"},
    )
    claimed = await work_items.claim_due_candidate(
        worker_id="w1", work_item_id=item.work_item_id, lease_seconds=30
    )
    assert claimed is not None
    ctx = work_execution.build_work_execution_context(
        work_item=claimed,
        logical_step_key="capability:0",
    )
    assert isinstance(ctx, WorkExecutionContext)
    assert ctx.work_item_id == claimed.work_item_id
    assert ctx.attempt == 1
    assert ctx.run_id == work_execution.deterministic_run_id(
        work_item_id=claimed.work_item_id, attempt=1
    )
    assert ctx.lease_token == claimed.lease_token
    assert ctx.lease_fence == claimed.lease_fence


@pytest.mark.asyncio
async def test_effect_boundary_rejects_cancel_deadline_lease_loss() -> None:
    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="wec-u-2",
        workspace_id="wec-ws-2",
        idempotency_key="wec-gate",
        input_payload={"capability_key": "a"},
    )
    claimed = await work_items.claim_due_candidate(
        worker_id="w1", work_item_id=item.work_item_id, lease_seconds=30
    )
    assert claimed is not None
    ctx = work_execution.build_work_execution_context(
        work_item=claimed, logical_step_key="capability:0"
    )
    await work_execution.assert_effect_boundary_allowed(ctx)

    await work_items.cancel_work_item(claimed.work_item_id)
    with pytest.raises(WorkError) as exc:
        await work_execution.assert_effect_boundary_allowed(ctx)
    assert exc.value.code == "work.cancelled"

    # Fresh claim path for lease loss.
    item2 = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="wec-u-3",
        workspace_id="wec-ws-3",
        idempotency_key="wec-lease",
        input_payload={"capability_key": "a"},
    )
    claimed2 = await work_items.claim_due_candidate(
        worker_id="w1", work_item_id=item2.work_item_id, lease_seconds=30
    )
    assert claimed2 is not None
    ctx2 = work_execution.build_work_execution_context(
        work_item=claimed2, logical_step_key="capability:0"
    )
    stale = ctx2.model_copy(update={"lease_token": "stale"})
    with pytest.raises(WorkError) as exc2:
        await work_execution.assert_effect_boundary_allowed(stale)
    assert exc2.value.code == "work.lease_lost"


def test_logical_step_conflict_fails_closed() -> None:
    meta: dict = {}
    meta = work_execution.persist_logical_step_slot(
        meta,
        logical_step_key="provider:0",
        capability_key="integral_list_entries",
        input_fingerprint="fp-a",
    )
    # Same slot + same fingerprint reuses.
    meta2 = work_execution.persist_logical_step_slot(
        meta,
        logical_step_key="provider:0",
        capability_key="integral_list_entries",
        input_fingerprint="fp-a",
    )
    assert meta2["logical_steps"]["provider:0"]["input_fingerprint"] == "fp-a"
    with pytest.raises(WorkError) as exc:
        work_execution.persist_logical_step_slot(
            meta2,
            logical_step_key="provider:0",
            capability_key="integral_list_entries",
            input_fingerprint="fp-b",
        )
    assert exc.value.code == "work.logical_step_conflict"


def test_non_replayable_adapter_rejected() -> None:
    with pytest.raises(WorkError) as exc:
        work_execution.assert_adapter_replayable(source="unknown", capability_key="x")
    assert exc.value.code == "work.non_replayable_effect"
    work_execution.assert_adapter_replayable(
        source="core", capability_key="integral_list_entries"
    )


def test_capability_result_receipt_refs_are_durable_pointers_only() -> None:
    from types import SimpleNamespace

    result = SimpleNamespace(
        receipt=SimpleNamespace(run_id="run-1", step_key="capability:1"),
        data={"operation_receipt": {"id": "o.OperationExecutionReceipt.1"}},
    )
    assert work_execution.receipt_refs_from_capability_result(result) == [
        "runstep:run-1:capability:1",
        "operation:o.OperationExecutionReceipt.1",
    ]
