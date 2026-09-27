"""Postgres graph transaction scope for command execution.

Prefers ``jvspatial.graph_transaction`` (0.0.22+). Older wheels keep the
local bind so CI on the current pin still hosts a real transaction. The
yielded handle is the transaction itself so receipt and outbox writes
share the same commit.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from jvspatial.core.context import GraphContext, scoped_default_context_async
from jvspatial.db import get_prime_database

try:
    from jvspatial.core.context import TransactionUnavailable as _JvTxnUnavailable
    from jvspatial.core.context import graph_transaction as _jv_graph_transaction
except ImportError:  # jvspatial < 0.0.22 — pin still 0.0.21 until bump
    _JvTxnUnavailable = None
    _jv_graph_transaction = None


class OperationTransactionUnavailable(RuntimeError):
    """Raised when the configured store cannot host a graph transaction."""


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
    if _jv_graph_transaction is not None:
        # A missing argument must use the prime store. graph_transaction(None)
        # probes a bare GraphContext, which refuses an auto-created manager.
        bound = database or get_prime_database()
        try:
            async with _jv_graph_transaction(bound) as ctx:
                yield ctx.database
        except _JvTxnUnavailable as exc:
            raise OperationTransactionUnavailable(str(exc)) from exc
        return

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


__all__ = [
    "OperationTransactionUnavailable",
    "graph_transaction_available",
    "postgres_graph_transaction",
]
