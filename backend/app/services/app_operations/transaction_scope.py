"""Postgres graph transaction scope for command execution.

Uses the public ``jvspatial.graph_transaction`` API. The yielded native
transaction handle lets receipt, outbox, and graph reads/writes share one
commit without a Core-side database compatibility adapter.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from time import perf_counter
from typing import Any, AsyncIterator, Iterator

from anyio import CancelScope
from jvspatial.core.context import TransactionUnavailable as _JvTxnUnavailable
from jvspatial.core.context import graph_transaction as _jv_graph_transaction
from jvspatial.db import get_prime_database

from app.middleware.permissions_cache import reset_permissions_cache

_logger = logging.getLogger(__name__)
_write_lock_timings = os.getenv("INTEGRAL_WRITE_LOCK_TIMINGS") == "1"


class OperationTransactionUnavailable(RuntimeError):
    """Raised when the configured store cannot host a graph transaction."""


class OperationTransactionRolledBack(OperationTransactionUnavailable):
    """A supported transaction was rolled back after a nested failure."""


@dataclass
class _TransactionScope:
    database: Any
    handle: Any
    owner: Any
    failed: bool = False
    failure: BaseException | None = None
    write_action: str | None = None
    lock_timings: dict[str, tuple[float, float, int]] | None = None


_active_transaction: ContextVar[_TransactionScope | None] = ContextVar(
    "integral_graph_transaction", default=None
)


def trace_entry_write(action: str) -> None:
    """Enable timings for the owning transaction's first entry effect."""
    if not _write_lock_timings:
        return
    active = _active_transaction.get()
    if active is not None and active.owner is asyncio.current_task():
        if active.write_action is None:
            active.write_action = action
            active.lock_timings = {}


@contextmanager
def measure_write_lock(scope: str, *, shared: bool = False) -> Iterator[None]:
    """Record the acquisition round trip and first acquisition for each scope."""
    active = _active_transaction.get() if _write_lock_timings else None
    timings = (
        active.lock_timings
        if active and active.owner is asyncio.current_task()
        else None
    )
    if timings is None:
        yield
        return
    start = perf_counter()
    yield
    acquired = perf_counter()
    key = f"{scope}.{'shared' if shared else 'exclusive'}"
    first, elapsed, calls = timings.get(key, (acquired, 0.0, 0))
    timings[key] = (first, elapsed + acquired - start, calls + 1)


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


def _matches_active_database(database: Any, active: _TransactionScope) -> bool:
    allowed = {id(active.database)}
    current = active.handle
    while current is not None and id(current) not in allowed:
        allowed.add(id(current))
        current = getattr(current, "inner", None)
    current = database
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        if id(current) in allowed:
            return True
        seen.add(id(current))
        current = getattr(current, "inner", None)
    return False


def graph_transaction_available(database: Any | None = None) -> bool:
    """Return whether the prime store exposes begin/commit/rollback."""
    active = _active_transaction.get()
    if active is not None:
        if active.owner is not asyncio.current_task():
            raise OperationTransactionUnavailable(
                "An inherited transaction cannot be joined by another task"
            )
        if database is None or _matches_active_database(database, active):
            return True
    db = _transaction_database(database or get_prime_database())
    required = (
        "begin_transaction",
        "commit_transaction",
        "rollback_transaction",
    )
    return db is not None and all(
        callable(getattr(db, name, None)) for name in required
    )


def current_graph_transaction_database(*, required: bool = True) -> Any:
    """Return this task's transaction; optional lookup still rejects invalid scopes."""
    active = _active_transaction.get()
    if active is None and not required:
        require_outside_graph_transaction()
        return None
    if active is None or active.owner is not asyncio.current_task() or active.failed:
        raise OperationTransactionUnavailable(
            "A current task-owned transaction is required"
        )
    return active.handle


def require_outside_graph_transaction() -> None:
    """Keep result recovery outside a caller's effect/receipt commit boundary."""
    from jvspatial.core.context import get_default_context
    from jvspatial.db.transaction import Transaction

    # A caller can use jvspatial directly, without Core's ContextVar. Inspect
    # the bound handle as well; opening a second pool transaction would escape
    # that caller's atomic boundary. Refuse even a closed bound handle.
    database = get_default_context().database
    seen: set[int] = set()
    while database is not None and id(database) not in seen:
        seen.add(id(database))
        if isinstance(database, Transaction):
            raise OperationTransactionUnavailable(
                "Recorded output recovery must run outside effect transactions"
            )
        database = getattr(database, "inner", None)
    if _active_transaction.get() is not None:
        raise OperationTransactionUnavailable(
            "Recorded output recovery must run outside effect transactions"
        )


@asynccontextmanager
async def _transaction_with_shielded_lifecycle(database: Any) -> AsyncIterator[Any]:
    """Keep ASGI disconnect cancellation out of connection acquisition/cleanup.

    The caller's transaction body remains cancellable. Enter and exit run in
    the same task so framework ContextVar tokens retain their owner; shielding
    only these boundaries lets rollback finish before pool release.
    """
    manager = _jv_graph_transaction(database)
    with CancelScope(shield=True):
        context = await manager.__aenter__()
    try:
        yield context
    except BaseException as exc:
        with CancelScope(shield=True):
            if not await manager.__aexit__(type(exc), exc, exc.__traceback__):
                raise
    else:
        with CancelScope(shield=True):
            await manager.__aexit__(None, None, None)


@asynccontextmanager
async def postgres_graph_transaction(
    database: Any | None = None,
) -> AsyncIterator[Any]:
    """Bind a graph transaction to Node/Edge writes for this task.

    The yielded transaction is available to the caller for receipt/outbox
    writes. Commit happens only after the caller exits successfully; any
    exception rolls back graph rows and raw transaction writes together.
    """
    active = _active_transaction.get()
    if active is not None:
        if active.owner is not asyncio.current_task():
            raise OperationTransactionUnavailable(
                "An inherited transaction cannot be joined by another task"
            )
        if database is not None:
            matches_active = _matches_active_database(database, active)
            if not matches_active:
                raise OperationTransactionUnavailable(
                    "Cannot join a different " "database"
                )
        try:
            yield active.handle
        except BaseException as exc:
            # Catching a nested failure must not commit its partial mutations.
            active.failed = True
            if active.failure is None:
                active.failure = exc
            raise
        return

    # A missing argument must use the prime store, not an auto-created manager.
    bound = database or get_prime_database()
    concrete = _transaction_database(bound)
    adapter = concrete
    frame = None
    body_end = None
    committed = False
    try:
        async with _transaction_with_shielded_lifecycle(adapter) as ctx:
            # Discard authorization memo entries so reads inside this scope
            # observe grants written on the transaction's connection.
            reset_permissions_cache()
            owner = asyncio.current_task()
            frame = _TransactionScope(concrete, ctx.database, owner)
            token = _active_transaction.set(frame)
            try:
                yield ctx.database
                if frame.failed:
                    raise OperationTransactionRolledBack(
                        "Nested transaction failure requires rollback"
                    ) from frame.failure
            finally:
                _active_transaction.reset(token)
                if frame.lock_timings is not None:
                    body_end = perf_counter()
        committed = True
    except _JvTxnUnavailable as exc:
        raise OperationTransactionUnavailable(str(exc)) from exc
    finally:
        if (
            frame is not None
            and frame.lock_timings is not None
            and body_end is not None
        ):
            finished = perf_counter()
            _logger.info(
                "entry_write_locks action=%s committed=%s transaction_exit_ms=%.3f locks=%s",
                frame.write_action,
                committed,
                (finished - body_end) * 1000,
                {
                    scope: {
                        "acquire_ms": round(elapsed * 1000, 3),
                        "held_through_exit_ms": round((finished - first) * 1000, 3),
                        "calls": calls,
                    }
                    for scope, (first, elapsed, calls) in frame.lock_timings.items()
                },
            )
        # Do not retain nodes bound to a completed or rolled-back transaction.
        reset_permissions_cache()


__all__ = [
    "OperationTransactionUnavailable",
    "graph_transaction_available",
    "current_graph_transaction_database",
    "postgres_graph_transaction",
]
