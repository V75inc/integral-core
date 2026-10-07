"""Postgres graph transaction scope for command execution.

Uses the public ``jvspatial.graph_transaction`` on the pinned 0.1.1 wheel.
The yielded handle is the transaction itself so receipt and outbox writes
share the same commit. Older unsupported wheels fail at import rather than
silently switching to another transaction implementation.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from jvspatial.core.context import TransactionUnavailable as _JvTxnUnavailable
from jvspatial.core.context import graph_transaction as _jv_graph_transaction
from jvspatial.db import get_prime_database


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
    # A missing argument must use the prime store, not an auto-created manager.
    bound = database or get_prime_database()
    try:
        async with _jv_graph_transaction(bound) as ctx:
            yield ctx.database
    except _JvTxnUnavailable as exc:
        raise OperationTransactionUnavailable(str(exc)) from exc


__all__ = [
    "OperationTransactionUnavailable",
    "graph_transaction_available",
    "postgres_graph_transaction",
]
