"""Stable boundary errors for durable App-operation receipt failures."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.api.errors import (
    BadRequestError,
    OperationIdempotencyConflictError,
    OperationReceiptRecoveryError,
    OperationTransactionUnavailableError,
)
from app.schemas.policy import Decision
from app.services.app_operations.dispatch import invoke_app_operation
from app.services.app_operations.execution_receipts import (
    OperationReceiptConflict,
    OperationReceiptIncomplete,
)
from app.services.app_operations.transaction_scope import (
    OperationTransactionUnavailable,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "error_type", "error_code"),
    [
        (
            OperationReceiptConflict("payload mismatch"),
            OperationIdempotencyConflictError,
            "idempotency_conflict",
        ),
        (
            OperationReceiptIncomplete("receipt not complete"),
            OperationReceiptRecoveryError,
            "operation_receipt_incomplete",
        ),
        (
            OperationTransactionUnavailable("no transaction"),
            OperationTransactionUnavailableError,
            "operation_transaction_unavailable",
        ),
    ],
)
async def test_durable_receipt_failures_have_stable_boundary_errors(
    failure, error_type, error_code
) -> None:
    """Broker adapters can preserve a deterministic public error code."""
    app = type(
        "AppStub",
        (),
        {
            "id": "n.App.receipt-errors",
            "workspace_id": "ws-receipt",
            "lifecycle_state": "active",
        },
    )()
    spec = {
        "key": "write",
        "kind": "execute",
        "policy_action": "entry.create",
        "input_schema": {},
        "handler_ref": "unused:handler",
    }

    with (
        patch(
            "app.services.app_operations.dispatch.App.get",
            new=AsyncMock(return_value=app),
        ),
        patch(
            "app.services.app_operations.dispatch.can_access_workspace",
            new=AsyncMock(return_value="owner"),
        ),
        patch(
            "app.services.app_operations.dispatch.resolve_role",
            new=AsyncMock(return_value="owner"),
        ),
        patch(
            "app.services.app_operations.dispatch.get_app_operation",
            return_value=spec,
        ),
        patch(
            "app.services.app_operations.dispatch.policy_evaluate",
            new=AsyncMock(return_value=Decision(allowed=True, reason="test")),
        ),
        patch(
            "app.services.app_operations.execution_receipts.execute_operation_once",
            new=AsyncMock(side_effect=failure),
        ),
    ):
        with pytest.raises(error_type) as raised:
            await invoke_app_operation(
                user_id="u-receipt",
                workspace_id="ws-receipt",
                app_id=app.id,
                operation_key="write",
                payload={"value": "x"},
                idempotency_key="receipt-error-key",
            )

    assert raised.value.error_code == error_code


@pytest.mark.asyncio
async def test_execute_kind_requires_a_durable_identity_even_with_default_policy() -> (
    None
):
    """A command cannot become an in-memory write through ``app.read`` defaulting."""
    app = type(
        "AppStub",
        (),
        {
            "id": "n.App.defaulted-command",
            "workspace_id": "ws-receipt",
            "lifecycle_state": "active",
        },
    )()
    spec = {
        "key": "write",
        "kind": "execute",
        "input_schema": {},
        "handler_ref": "unused:handler",
    }

    with (
        patch(
            "app.services.app_operations.dispatch.App.get",
            new=AsyncMock(return_value=app),
        ),
        patch(
            "app.services.app_operations.dispatch.can_access_workspace",
            new=AsyncMock(return_value="owner"),
        ),
        patch(
            "app.services.app_operations.dispatch.resolve_role",
            new=AsyncMock(return_value="owner"),
        ),
        patch(
            "app.services.app_operations.dispatch.get_app_operation",
            return_value=spec,
        ),
        patch(
            "app.services.app_operations.dispatch.policy_evaluate",
            new=AsyncMock(return_value=Decision(allowed=True, reason="test")),
        ),
    ):
        with pytest.raises(BadRequestError) as raised:
            await invoke_app_operation(
                user_id="u-receipt",
                workspace_id="ws-receipt",
                app_id=app.id,
                operation_key="write",
                payload={"value": "x"},
            )

    assert raised.value.details["error_code"] == "operation_idempotency_required"
