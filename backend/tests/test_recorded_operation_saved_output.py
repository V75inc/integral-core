"""Saved output corruption cannot become a fabricated readable response."""

import json
from contextlib import asynccontextmanager

import pytest

from app.contracts.operations import OperationIdentity
from app.services.app_operations import recorded_results
from app.services.app_operations.execution_receipts import (
    OperationOutcomeObservation,
    _receipt_document,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize(
    "bad",
    [
        {"object_refs": "invalid"},
        {"object_refs": ["invalid"]},
        {"evidence": "invalid"},
        {"output": []},
        {"app_id": "other"},
        {"operation_key": "other"},
        {"unknown": "private"},
    ],
)
async def test_unusable_saved_envelope_is_withheld(monkeypatch, bad):
    identity = OperationIdentity.create(
        workspace_id="workspace",
        app_id="app",
        operation_key="record",
        principal_id="actor",
        idempotency_key="same-intent",
    )
    result = {"app_id": "app", "operation_key": "record", "output": {}, **bad}
    row = _receipt_document(
        identity=identity,
        request_hash="hash",
        status="succeeded",
        result_json=json.dumps(result),
    )

    class Handle:
        async def get(self, collection, key):
            return row

    @asynccontextmanager
    async def transaction():
        yield Handle()

    monkeypatch.setattr(recorded_results, "postgres_graph_transaction", transaction)
    assert (
        await recorded_results._saved_result(
            OperationOutcomeObservation(identity), "hash"
        )
        is None
    )
