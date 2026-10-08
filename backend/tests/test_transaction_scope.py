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
    assert callable(transaction.count)


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


@pytest.mark.asyncio
async def test_transaction_count_reads_bounded_pages_without_hydrating_entities():
    from app.services.app_operations.transaction_scope import _SerializedTransaction

    query = {"entity": "Entry", "context.track_id": "track"}

    class Connection:
        calls = []

        async def find(self, collection, page_query, *, limit=None, sort=None):
            self.calls.append((collection, page_query, limit, sort))
            assert collection == "node"
            assert limit == 256
            assert sort == [("id", 1)]
            after = ""
            if page_query != query:
                assert page_query["$and"][0] == query
                after = page_query["$and"][1]["id"]["$gt"]
            return [
                {"id": f"n.Entry.{i:04d}"}
                for i in range(600)
                if f"n.Entry.{i:04d}" > after
            ][:limit]

    connection = Connection()
    assert await _SerializedTransaction(connection).count("node", query) == 600
    assert len(connection.calls) == 3


@pytest.mark.asyncio
async def test_transaction_count_prefers_public_native_aggregate():
    from app.services.app_operations.transaction_scope import _SerializedTransaction

    class Connection:
        async def count(self, collection, query):
            assert collection == "node"
            assert query == {"entity": "Entry"}
            return 42

        async def find(self, *args, **kwargs):
            raise AssertionError("native aggregate must not scan records")

    assert (
        await _SerializedTransaction(Connection()).count("node", {"entity": "Entry"})
        == 42
    )


@pytest.mark.asyncio
async def test_transaction_count_refuses_a_nonadvancing_cursor():
    from app.services.app_operations.transaction_scope import _SerializedTransaction

    class Connection:
        async def find(self, *args, **kwargs):
            return [{"id": "same"}] * 256

    with pytest.raises(RuntimeError, match="cursor did not advance"):
        await _SerializedTransaction(Connection()).count("node", {})
