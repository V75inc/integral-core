"""Nested App operations retain the enclosing durable-operation identity."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.app_operations.context import OperationContext


@pytest.mark.asyncio
async def test_operation_context_propagates_idempotency_key_to_nested_operation():
    ctx = OperationContext(
        user_id="u-nested",
        workspace_id="ws-nested",
        scope="operation:app-nested:outer",
        app_id="app-nested",
        operation_key="outer",
        idempotency_key="logical-operation-key",
        correlation_id="correlation-nested",
    )
    with patch(
        "app.services.app_operations.dispatch.invoke_app_operation",
        new=AsyncMock(return_value={"ok": True}),
    ) as invoke:
        result = await ctx.invoke("inner", {"value": "x"})

    assert result == {"ok": True}
    assert invoke.await_args.kwargs == {
        "user_id": "u-nested",
        "workspace_id": "ws-nested",
        "app_id": "app-nested",
        "operation_key": "inner",
        "payload": {"value": "x"},
        "idempotency_key": "logical-operation-key",
        "correlation_id": "correlation-nested",
    }
