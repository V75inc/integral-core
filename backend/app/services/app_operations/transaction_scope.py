"""Postgres graph transaction scope for future command execution.

The jvspatial transaction handle exposes the same persistence methods used by
``GraphContext``.  Binding that handle as the task-local default context lets
ordinary ``Node.create`` and ``Node.connect`` calls participate in one
transaction.  This module is deliberately narrow: it provides only the
transaction boundary, not command claiming, receipts, or outbox delivery.

Those higher-level concerns must be added together before the app-operation
dispatcher can advertise atomic command execution.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from jvspatial.core.context import GraphContext, scoped_default_context_async
from jvspatial.db import get_prime_database


class OperationTransactionUnavailable(RuntimeError):
    """Raised when the configured store cannot host a graph transaction."""


def _transaction_database(database: Any) -> Any:
    """Return the concrete database beneath observable/cache decorators."""
    current = database
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if type(current).__name__ == "PostgresDB":
            return current
        nested = getattr(current, "inner", None)
        if nested is None:
            break
        current = nested
    return database


@asynccontextmanager
async def postgres_graph_transaction(
    database: Any | None = None,
) -> AsyncIterator[Any]:
    """Bind a Postgres transaction to graph persistence for this task.

    The yielded transaction is intentionally available to the caller for
    receipt/outbox writes.  Commit happens only after the caller exits
    successfully; any exception rolls back graph rows and raw transaction
    writes together.
    """
    db = _transaction_database(database or get_prime_database())
    required = ("begin_transaction", "commit_transaction", "rollback_transaction")
    if not all(callable(getattr(db, name, None)) for name in required):
        raise OperationTransactionUnavailable(
            "app operation transactions require a database with transaction support"
        )

    transaction = await db.begin_transaction()
    graph_context = GraphContext(transaction)
    try:
        async with scoped_default_context_async(graph_context):
            yield transaction
        await db.commit_transaction(transaction)
    except BaseException:
        await db.rollback_transaction(transaction)
        raise


__all__ = ["OperationTransactionUnavailable", "postgres_graph_transaction"]
