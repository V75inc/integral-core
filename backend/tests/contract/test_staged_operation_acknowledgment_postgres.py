"""Acknowledgment closes a fresh review without borrowing original effect proof."""

import copy

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive import staging, staging_store
from app.agentive.services import staging_apply
from app.services.app_operations import dispatch
from app.services.app_operations.registry import unregister_app_operations
from tests.contract.test_staged_app_operation_expiry_postgres import (
    advance_admission_clock,
)
from tests.contract.test_staged_app_operation_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def earlier_http_operation(actor, workspace, app, change):
    return await dispatch.invoke_app_operation(
        user_id=actor.id,
        workspace_id=workspace.id,
        app_id=app.id,
        operation_key="record",
        payload={},
        idempotency_key=change.payload["operation_idempotency_key"],
    )


@pytest.mark.parametrize(
    "invalid", ["missing_contract", "different_contract", "invalid_decision"]
)
async def test_acknowledgment_requires_independent_valid_original_proof(
    postgres_raw_db, monkeypatch, invalid
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, ws, app, change, calls = await proposal(monkeypatch)
        try:
            first = await earlier_http_operation(actor, ws, app, change)
            receipt_id = first["operation_receipt"]["id"]
            updates = {
                "missing_contract": {
                    "$unset": {"context.effect_contract_fingerprint": ""}
                },
                "different_contract": {
                    "$set": {"context.effect_contract_fingerprint": "f" * 64}
                },
                "invalid_decision": {
                    "$set": {"context.decision_intent_fingerprint": None}
                },
            }
            await postgres_raw_db.find_one_and_update(
                "object", {"id": receipt_id}, updates[invalid]
            )
            before = await postgres_raw_db.get("object", receipt_id)
            await staging.bless_token(user_id=actor.id, token=change.token)
            with pytest.raises(staging.StagingError) as refused:
                await staging_apply.execute_blessed_change(
                    user_id=actor.id, token=change.token
                )
            assert refused.value.code == "application_reconciliation_required"
            assert calls == ["effect"]
            assert await postgres_raw_db.get("object", receipt_id) == before
            assert (
                await staging_store.load_decision_state(change.token, actor.id) is None
            )
        finally:
            unregister_app_operations(ws.id, app.id)


async def test_expired_new_review_cannot_acknowledge_an_earlier_operation(
    postgres_raw_db, monkeypatch
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, ws, app, change, calls = await proposal(monkeypatch)
        try:
            first = await earlier_http_operation(actor, ws, app, change)
            before = await postgres_raw_db.get(
                "object", first["operation_receipt"]["id"]
            )
            await staging.bless_token(user_id=actor.id, token=change.token)
            advance_admission_clock(monkeypatch, change)
            with pytest.raises(staging.StagingError) as refused:
                await staging_apply.execute_blessed_change(
                    user_id=actor.id, token=change.token, staged=change
                )
            assert refused.value.code == "expired"
            assert calls == ["effect"]
            assert (
                await postgres_raw_db.get("object", first["operation_receipt"]["id"])
                == before
            )
            assert (
                await staging_store.load_decision_state(change.token, actor.id) is None
            )
        finally:
            unregister_app_operations(ws.id, app.id)


async def test_lost_acknowledgment_closure_uses_terminal_intent_without_a_handler(
    postgres_raw_db, monkeypatch
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, ws, app, change, calls = await proposal(monkeypatch)
        try:
            original = await earlier_http_operation(actor, ws, app, change)
            receipt_id = original["operation_receipt"]["id"]
            before = await postgres_raw_db.get("object", receipt_id)
            await staging.bless_token(user_id=actor.id, token=change.token)
            stale = copy.deepcopy(change)

            def forbidden(*args):
                pytest.fail("Acknowledgment resolved an operation handler")

            monkeypatch.setattr(dispatch, "resolve_handler", forbidden)
            first = await staging_apply.execute_blessed_change(
                user_id=actor.id, token=change.token
            )
            assert first["consumed"] is True, first
            assert first["execute_result"]["already_completed"] is True
            staging._tokens.clear()
            second = await staging_apply.execute_blessed_change(
                user_id=actor.id, token=change.token, staged=stale
            )
            assert second["consumed"] is True, second
            assert second["execute_result"]["already_completed"] is True
            assert second["execute_result"]["operation_receipt"]["id"] == receipt_id
            assert calls == ["effect"]
            assert await postgres_raw_db.get("object", receipt_id) == before
        finally:
            unregister_app_operations(ws.id, app.id)
