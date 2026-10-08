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


@pytest.mark.asyncio
async def test_transaction_handle_serializes_fanout_and_releases_after_error():
    import asyncio

    from app.services.app_operations.transaction_scope import _SerializedTransaction

    class Connection:
        active = 0
        maximum = 0

        async def find(self, value):
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            try:
                await asyncio.sleep(0)
                if value == "fail":
                    raise ValueError("synthetic read failure")
                return value
            finally:
                self.active -= 1

    connection = Connection()
    transaction = _SerializedTransaction(connection)
    assert await asyncio.gather(*(transaction.find(i) for i in range(12))) == list(
        range(12)
    )
    assert connection.maximum == 1
    with pytest.raises(ValueError, match="synthetic read failure"):
        await transaction.find("fail")
    assert await transaction.find("after") == "after"
    assert getattr(transaction, "count", None) is None


@pytest.mark.asyncio
async def test_transaction_find_one_uses_bounded_held_connection_read():
    from app.services.app_operations.transaction_scope import _SerializedTransaction

    class Connection:
        calls = []

        async def find(self, collection, query, *, limit=None):
            self.calls.append((collection, query, limit))
            return [{"id": "record"}] if query else []

    connection = Connection()
    transaction = _SerializedTransaction(connection)
    assert await transaction.find_one("object", {"context.token": "token"}) == {
        "id": "record"
    }
    assert await transaction.find_one("object", {}) is None
    assert connection.calls == [
        ("object", {"context.token": "token"}, 1),
        ("object", {}, 1),
    ]
