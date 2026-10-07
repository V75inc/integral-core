"""Public jvspatial transaction seam for canonical entry commands."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from weakref import WeakValueDictionary

from jvspatial.core.context import get_default_context, graph_transaction
from jvspatial.db.postgres import PostgresTransaction

_local_locks: WeakValueDictionary[tuple[int, int, str], asyncio.Lock] = (
    WeakValueDictionary()
)


@asynccontextmanager
async def entry_write_scope(identity: str):
    """Join the caller's PG transaction or open an entry command transaction."""
    database = get_default_context().database
    concrete = database
    while getattr(concrete, "inner", None) is not None:
        concrete = concrete.inner
    if isinstance(concrete, PostgresTransaction):
        # The caller's command or connector fence already owns this commit.
        yield get_default_context()
    elif all(
        callable(getattr(concrete, key, None))
        for key in ("begin_transaction", "commit_transaction", "rollback_transaction")
    ):
        async with graph_transaction(database) as graph:
            yield graph
    elif type(concrete).__module__ in {"jvspatial.db.jsondb", "jvspatial.memory"}:
        # Single-process stores cannot promise transaction rollback or worker
        # fencing. This only serializes local entry commands; connector sync
        # separately refuses these stores outside testing/development.
        key = (id(concrete), id(asyncio.get_running_loop()), identity)
        # Hold a strong reference for owners/waiters, but release idle entry
        # identities so a long-lived local store cannot grow this map forever.
        lock = _local_locks.setdefault(key, asyncio.Lock())
        async with lock:
            yield get_default_context()
    else:
        raise RuntimeError(
            "Entry commands require transactional or single-process storage"
        )
