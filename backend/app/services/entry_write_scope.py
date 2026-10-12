"""Public jvspatial transaction seam for canonical entry commands."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any
from weakref import WeakValueDictionary

from jvspatial.core.context import get_default_context
from jvspatial.db.postgres import PostgresTransaction

_local_locks: WeakValueDictionary[tuple[int, int, str], asyncio.Lock] = (
    WeakValueDictionary()
)

_active_local: ContextVar[tuple[object, object, frozenset[str]] | None] = ContextVar(
    "integral_local_entry_commands", default=None
)


def is_postgres_entry_transaction(database: Any) -> bool:
    """Recognize both the public handle and Core's serialized transaction."""
    concrete = database
    seen: set[int] = set()
    while getattr(concrete, "inner", None) is not None and id(concrete) not in seen:
        seen.add(id(concrete))
        concrete = concrete.inner
    return isinstance(concrete, PostgresTransaction)


@asynccontextmanager
async def entry_write_scope(identity: str):
    """Join the caller's PG transaction or open an entry command transaction."""
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
        postgres_graph_transaction,
    )

    database = get_default_context().database
    concrete = database
    while getattr(concrete, "inner", None) is not None:
        concrete = concrete.inner
    if is_postgres_entry_transaction(database) or graph_transaction_available(database):
        # Join the shared owner/task fence, or open its serialized handle.
        # Fresh entity find_one reads and concurrent graph I/O must remain on
        # this connection; a raw pinned PostgresTransaction lacks find_one.
        async with postgres_graph_transaction(database):
            yield get_default_context()
    elif type(concrete).__module__ in {"jvspatial.db.jsondb", "jvspatial.memory"}:
        # Single-process stores cannot promise transaction rollback or worker
        # fencing. This only serializes local entry commands; connector sync
        # separately refuses these stores outside testing/development.
        active = _active_local.get()
        task = asyncio.current_task()
        if (
            active is not None
            and active[0] is task
            and active[1] is concrete
            and identity in active[2]
        ):
            yield get_default_context()
            return
        key = (id(concrete), id(asyncio.get_running_loop()), identity)
        # Hold a strong reference for owners/waiters, but release idle entry
        # identities so a long-lived local store cannot grow this map forever.
        lock = _local_locks.setdefault(key, asyncio.Lock())
        async with lock:
            inherited = (
                active[2]
                if active is not None and active[0] is task and active[1] is concrete
                else frozenset()
            )
            token = _active_local.set((task, concrete, inherited | {identity}))
            try:
                yield get_default_context()
            finally:
                _active_local.reset(token)
    else:
        raise RuntimeError(
            "Entry commands require transactional or single-process storage"
        )
