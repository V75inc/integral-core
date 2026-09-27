"""Graph transaction scope binds the prime store when none is passed."""

from contextlib import asynccontextmanager

import pytest


@pytest.mark.asyncio
async def test_omitted_database_uses_the_prime_store(monkeypatch):
    prime = object()
    seen = {}

    @asynccontextmanager
    async def fake(database):
        seen["database"] = database

        class _Ctx:
            database = "txn-handle"

        yield _Ctx()

    monkeypatch.setattr(
        "app.services.app_operations.transaction_scope._jv_graph_transaction",
        fake,
    )
    monkeypatch.setattr(
        "app.services.app_operations.transaction_scope.get_prime_database",
        lambda: prime,
    )
    from app.services.app_operations.transaction_scope import (
        postgres_graph_transaction,
    )

    async with postgres_graph_transaction() as handle:
        assert handle == "txn-handle"
    assert seen["database"] is prime
