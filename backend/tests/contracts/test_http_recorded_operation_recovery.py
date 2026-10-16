"""HTTP recovery uses authenticated scope and never enters the effect broker."""

from unittest.mock import AsyncMock, patch

import pytest

from app.api.app_extensions import invoke_operation
from app.contracts.runtime import ExecutionScope
from app.schemas.app_operations import AppOperationOutputWithheld
from app.services.app_operations.execution_receipts import (
    OperationReceiptConflict,
    OperationReceiptIncomplete,
)
from app.services.app_operations.transaction_scope import (
    OperationTransactionUnavailable,
)
from tests.contracts.test_http_execution_scope import _Request

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def scope():
    return ExecutionScope.create(
        principal_id="authenticated-human",
        workspace_id="selected-workspace",
        origin="http_extension_operation",
    )


def withheld():
    return AppOperationOutputWithheld(
        operation_receipt={"id": "o.OperationExecutionReceipt." + "a" * 64}
    )


async def test_original_completion_uses_bound_scope_before_snapshot_or_broker():
    request = _Request(
        {"input": {"value": "original"}}, {"Idempotency-Key": "original-intent"}
    )
    with (
        patch(
            "app.api.app_extensions.resolve_principal_id",
            return_value="authenticated-human",
        ),
        patch(
            "app.api.app_extensions.resolve_execution_scope_from_request",
            new=AsyncMock(return_value=scope()),
        ),
        patch(
            "app.services.app_operations.transaction_scope.graph_transaction_available",
            return_value=True,
        ),
        patch(
            "app.services.app_operations.recorded_results.recover_recorded_operation_response",
            new=AsyncMock(return_value=withheld()),
        ) as recover,
        patch(
            "app.agentive.services.execution_runs.build_capability_snapshot",
            new=AsyncMock(side_effect=AssertionError("snapshot loaded")),
        ),
        patch(
            "app.agentive.services.capability_broker.invoke_declared_capability",
            new=AsyncMock(side_effect=AssertionError("broker entered")),
        ),
    ):
        result = await invoke_operation(request, "original-app", "original-function")
    assert result == withheld().model_dump()
    args = recover.await_args.kwargs
    assert args["scope"] == scope()
    assert args["subject"].kind == "human"
    assert args["subject"].id == scope().principal_id
    assert args["payload"] == {"value": "original"}
    assert args["idempotency_key"] == "original-intent"


@pytest.mark.parametrize(
    "fault,code",
    [
        (OperationReceiptConflict("conflict"), "idempotency_conflict"),
        (OperationReceiptIncomplete("incomplete"), "operation_receipt_incomplete"),
        (
            OperationTransactionUnavailable("unavailable"),
            "operation_transaction_unavailable",
        ),
    ],
)
async def test_typed_recovery_failure_never_falls_through_to_execution(fault, code):
    with (
        patch(
            "app.api.app_extensions.resolve_principal_id",
            return_value="authenticated-human",
        ),
        patch(
            "app.api.app_extensions.resolve_execution_scope_from_request",
            new=AsyncMock(return_value=scope()),
        ),
        patch(
            "app.services.app_operations.transaction_scope.graph_transaction_available",
            return_value=True,
        ),
        patch(
            "app.services.app_operations.recorded_results.recover_recorded_operation_response",
            new=AsyncMock(side_effect=fault),
        ),
        patch(
            "app.agentive.services.capability_broker.invoke_declared_capability",
            new=AsyncMock(side_effect=AssertionError("broker entered")),
        ),
    ):
        with pytest.raises(Exception) as error:
            await invoke_operation(
                _Request({"input": {}}, {"Idempotency-Key": "original-intent"}),
                "app",
                "operation",
            )
        assert error.value.error_code == code


async def test_native_storage_outage_never_becomes_an_absent_operation():
    with (
        patch(
            "app.api.app_extensions.resolve_principal_id",
            return_value="authenticated-human",
        ),
        patch(
            "app.api.app_extensions.resolve_execution_scope_from_request",
            new=AsyncMock(return_value=scope()),
        ),
        patch(
            "app.services.app_operations.transaction_scope.graph_transaction_available",
            return_value=True,
        ),
        patch(
            "app.services.app_operations.recorded_results.recover_recorded_operation_response",
            new=AsyncMock(side_effect=RuntimeError("store unavailable")),
        ),
        patch(
            "app.agentive.services.capability_broker.invoke_declared_capability",
            new=AsyncMock(side_effect=AssertionError("broker entered")),
        ),
    ):
        with pytest.raises(RuntimeError, match="store unavailable"):
            await invoke_operation(
                _Request({"input": {}}, {"Idempotency-Key": "original-intent"}),
                "app",
                "operation",
            )


@pytest.mark.parametrize(
    "native,key", [(True, "original-intent"), (False, "original-intent"), (True, None)]
)
async def test_only_absence_or_unsupported_read_store_continues_to_normal_admission(
    native, key
):
    from types import SimpleNamespace

    body = {"input": {"value": "original"}}
    request = _Request(body, {"Idempotency-Key": key} if key else {})
    broker_result = SimpleNamespace(
        ok=True, receipt=None, data={"output": {"changed": True}}
    )
    with (
        patch(
            "app.api.app_extensions.resolve_principal_id",
            return_value="authenticated-human",
        ),
        patch(
            "app.api.app_extensions.resolve_execution_scope_from_request",
            new=AsyncMock(return_value=scope()),
        ),
        patch(
            "app.services.app_operations.transaction_scope.graph_transaction_available",
            return_value=native,
        ),
        patch(
            "app.services.app_operations.recorded_results.recover_recorded_operation_response",
            new=AsyncMock(return_value=None),
        ) as recover,
        patch(
            "app.agentive.services.execution_runs.build_capability_snapshot",
            new=AsyncMock(return_value={"apps": []}),
        ),
        patch(
            "app.agentive.services.capability_broker.invoke_declared_capability",
            new=AsyncMock(return_value=broker_result),
        ) as broker,
    ):
        result = await invoke_operation(request, "app", "operation")
    assert result["output"] == {"changed": True}
    assert broker.await_count == 1
    assert broker.await_args.kwargs["principal_id"] == scope().principal_id
    assert broker.await_args.kwargs["workspace_id"] == scope().workspace_id
    assert broker.await_args.kwargs["idempotency_key"] == key
    assert recover.await_count == int(native and key is not None)


async def test_request_cannot_supply_a_different_recovery_subject_or_workspace():
    from pydantic import ValidationError

    with (
        patch(
            "app.api.app_extensions.resolve_principal_id",
            return_value="authenticated-human",
        ),
        patch(
            "app.api.app_extensions.resolve_execution_scope_from_request",
            new=AsyncMock(return_value=scope()),
        ),
        patch(
            "app.services.app_operations.recorded_results.recover_recorded_operation_response",
            new=AsyncMock(),
        ) as recover,
    ):
        request = _Request(
            {"input": {}, "principal_id": "other", "workspace_id": "other"},
            {"Idempotency-Key": "same-intent"},
        )
        with pytest.raises(ValidationError):
            await invoke_operation(request, "app", "operation")
        recover.assert_not_awaited()
