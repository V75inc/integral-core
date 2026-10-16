"""A reviewed-declaration recovery cannot infer proof from private saved output."""

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.schemas.policy import Subject
from app.services.app_operations import recorded_results
from app.services.app_operations.execution_receipts import (
    OperationReceiptConflict,
    OperationReceiptIncomplete,
)
from app.services.app_operations.registry import unregister_app_operations
from tests.contract.test_recorded_operation_results_postgres import prepared

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


@pytest.mark.parametrize("change", ["none", "removed", "wrong_contract", "legacy"])
async def test_exact_review_proof_precedes_saved_output_disclosure(
    postgres_raw_db, monkeypatch, change
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, app, scope, args, first, calls = await prepared(monkeypatch)
        key = first["operation_receipt"]["id"]
        row = await postgres_raw_db.get("object", key)
        original_contract = row["context"]["effect_contract_fingerprint"]
        expected = original_contract
        if change == "removed":
            unregister_app_operations(scope.workspace_id, app.id)
        elif change == "wrong_contract":
            expected = "0" * 64 if original_contract != "0" * 64 else "1" * 64
        elif change == "legacy":
            del row["context"]["effect_contract_fingerprint"]
            await postgres_raw_db.save("object", row)
        if change != "none":

            async def no_private_result(*args):
                pytest.fail("Declaration proof decoded private saved output")

            monkeypatch.setattr(recorded_results, "_saved_result", no_private_result)
        before = await postgres_raw_db.get("object", key)
        try:
            arguments = dict(
                scope=scope,
                subject=Subject(kind="human", id=actor.id),
                expected_effect_contract_fingerprint=expected,
                **args,
            )
            if change in {"wrong_contract", "legacy"}:
                error = (
                    OperationReceiptConflict
                    if change == "wrong_contract"
                    else OperationReceiptIncomplete
                )
                with pytest.raises(error):
                    await recorded_results.recover_recorded_operation_response(
                        **arguments
                    )
            else:
                result = await recorded_results.recover_recorded_operation_response(
                    **arguments
                )
                assert result.operation_receipt is not None
                assert result.output_disposition == (
                    "withheld" if change == "removed" else "available"
                )
            assert calls == ["effect"]
            assert await postgres_raw_db.get("object", key) == before
        finally:
            unregister_app_operations(scope.workspace_id, app.id)
