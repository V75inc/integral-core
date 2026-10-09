"""Postgres graph transaction scope for command execution.

Uses the public ``jvspatial.graph_transaction`` on the pinned 0.1.1 wheel.
The yielded handle is the transaction itself so receipt and outbox writes
share the same commit. Older unsupported wheels fail at import rather than
silently switching to another transaction implementation.
"""

from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager
from functools import wraps
from typing import Any, AsyncIterator

from jvspatial.core.context import TransactionUnavailable as _JvTxnUnavailable
from jvspatial.core.context import graph_transaction as _jv_graph_transaction
from jvspatial.db import get_prime_database

from app.middleware.permissions_cache import reset_permissions_cache

_TRANSACTION_COUNT_PAGE_SIZE = 256


class OperationTransactionUnavailable(RuntimeError):
    """Raised when the configured store cannot host a graph transaction."""


class _SerializedTransaction:
    """Serialize public I/O on one transaction's held database connection.

    Graph services can fan out permission reads with asyncio.gather. An asyncpg
    transaction has one connection and cannot execute those reads concurrently.
    Keep the library-owned graph/cache/commit scope, while queuing its public
    database calls on that same transaction rather than escaping to the pool.
    """

    def __init__(self, transaction: Any) -> None:
        self.inner = transaction
        self._lock = asyncio.Lock()

    async def find_one(
        self, collection: str, query: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Supply the graph database convenience read on the held connection.

        The pinned transaction exposes find but not Database.find_one, which
        Object.find_one calls. Bound the equivalent read to one result and use
        the same serialization lock as every other transaction operation.
        """
        async with self._lock:
            records = await self.inner.find(collection, query, limit=1)
        return records[0] if records else None

    async def count(self, collection: str, query: dict[str, Any]) -> int:
        """Count on the held transaction without escaping to the pool.

        Prefer a public aggregate when supplied by the transaction. The pinned
        PostgreSQL handle lacks it, so use bounded, id-ordered public reads.
        This compatibility path scans matching records but never hydrates graph
        entities or loads the entire collection into one result list.
        """
        native_count = getattr(self.inner, "count", None)
        if callable(native_count):
            async with self._lock:
                return await native_count(collection, query)
        total = 0
        last_id: str | None = None
        while True:
            page_query = (
                {"$and": [query, {"id": {"$gt": last_id}}]}
                if last_id is not None
                else query
            )
            page = await self.find(
                collection,
                page_query,
                limit=_TRANSACTION_COUNT_PAGE_SIZE,
                sort=[("id", 1)],
            )
            total += len(page)
            if len(page) < _TRANSACTION_COUNT_PAGE_SIZE:
                return total
            next_id = page[-1].get("id")
            if not isinstance(next_id, str) or (
                last_id is not None and next_id <= last_id
            ):
                raise RuntimeError("Transaction count cursor did not advance")
            last_id = next_id

    def __getattr__(self, name: str) -> Any:
        value = getattr(self.inner, name)
        if not inspect.iscoroutinefunction(value):
            return value

        @wraps(value)
        async def serialized(*args: Any, **kwargs: Any) -> Any:
            async with self._lock:
                return await value(*args, **kwargs)

        return serialized


class _SerializedTransactionDatabase:
    """Let jvspatial create its normal GraphContext on a serialized handle."""

    def __init__(self, database: Any) -> None:
        self.inner = database

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def begin_transaction(self) -> _SerializedTransaction:
        return _SerializedTransaction(await self.inner.begin_transaction())

    async def commit_transaction(self, transaction: _SerializedTransaction) -> None:
        await self.inner.commit_transaction(transaction.inner)

    async def rollback_transaction(self, transaction: _SerializedTransaction) -> None:
        await self.inner.rollback_transaction(transaction.inner)


def _transaction_database(database: Any) -> Any:
    """Return the concrete database beneath observable/cache decorators."""
    current = database
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if callable(getattr(current, "begin_transaction", None)):
            return current
        nested = getattr(current, "inner", None)
        if nested is None:
            break
        current = nested
    return database


def graph_transaction_available(database: Any | None = None) -> bool:
    """True when the prime store exposes begin/commit/rollback."""
    db = _transaction_database(database or get_prime_database())
    required = ("begin_transaction", "commit_transaction", "rollback_transaction")
    return db is not None and all(
        callable(getattr(db, name, None)) for name in required
    )


@asynccontextmanager
async def postgres_graph_transaction(
    database: Any | None = None,
) -> AsyncIterator[Any]:
    """Bind a graph transaction to Node/Edge writes for this task.

    The yielded transaction is available to the caller for receipt/outbox
    writes. Commit happens only after the caller exits successfully; any
    exception rolls back graph rows and raw transaction writes together.
    """
    # A missing argument must use the prime store, not an auto-created manager.
    bound = database or get_prime_database()
    concrete = _transaction_database(bound)
    adapter = (
        _SerializedTransactionDatabase(concrete)
        if graph_transaction_available(concrete)
        else concrete
    )
    try:
        async with _jv_graph_transaction(adapter) as ctx:
            # Memoized User nodes retain the GraphContext that loaded them.
            # A pre-transaction node would read ownership through the pool and
            # miss edges written on this transaction's uncommitted connection.
            reset_permissions_cache()
            yield ctx.database
    except _JvTxnUnavailable as exc:
        raise OperationTransactionUnavailable(str(exc)) from exc
    finally:
        # Do not retain nodes bound to a completed or rolled-back transaction.
        reset_permissions_cache()


__all__ = [
    "OperationTransactionUnavailable",
    "graph_transaction_available",
    "postgres_graph_transaction",
]
