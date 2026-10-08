"""Actual-store capability broker reads within worker transactions."""

from __future__ import annotations

import uuid

import pytest
from jvspatial.core.context import GraphContext, set_default_context

from app.agentive.services.capability_broker import invoke
from app.schemas.capability_broker import ERR_RUN_NOT_FOUND, CapabilityInvocation
from app.services.app_operations.transaction_scope import postgres_graph_transaction


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_transactional_broker_missing_run_returns_structured_denial(
    postgres_raw_db,
):
    """The worker's transaction adapter must support the broker lookup path."""
    from jvspatial.core.context import _default_context_var

    token = set_default_context(GraphContext(database=postgres_raw_db))
    try:
        async with postgres_graph_transaction(postgres_raw_db):
            result = await invoke(
                CapabilityInvocation(
                    run_id=f"missing-{uuid.uuid4().hex}",
                    principal_id="synthetic-principal",
                    workspace_id="synthetic-workspace",
                    origin="chat",
                    capability_key="integral_get_attachment_text",
                )
            )
        assert result.ok is False
        assert result.error_code == ERR_RUN_NOT_FOUND
    finally:
        _default_context_var.reset(token)


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_transactional_run_and_receipt_queries_preserve_scope(postgres_raw_db):
    from jvspatial.core.context import _default_context_var

    from app.agentive.services.execution_runs import AgentRun, RunStep

    run_id = f"transactional-{uuid.uuid4().hex}"
    token = set_default_context(GraphContext(database=postgres_raw_db))
    try:
        run = await AgentRun.create(
            run_id=run_id, user_id="owner", workspace_id="scope"
        )
        step = await RunStep.create(
            run_id=run_id, step_key="read:1", idempotency_key="same"
        )
        async with postgres_graph_transaction(postgres_raw_db):
            loaded = await AgentRun.find_one({"run_id": run_id})
            receipt = await RunStep.find_one(
                {"run_id": run_id, "idempotency_key": "same"}
            )
            assert loaded is not None and loaded.id == run.id
            assert loaded.user_id == "owner" and loaded.workspace_id == "scope"
            assert receipt is not None and receipt.id == step.id
            assert await AgentRun.find_one({"run_id": "other-run"}) is None
            assert (
                await RunStep.find_one(
                    {"run_id": "other-run", "idempotency_key": "same"}
                )
                is None
            )
    finally:
        _default_context_var.reset(token)
