"""Bounded local inference admission, with interactive priority between batches.

Native work cannot be cancelled safely in a Python thread. Admission remains
occupied until actual completion even when its caller times out or disconnects.
There is one dedicated executor per process, not one per tenant or event loop.
"""

from __future__ import annotations

import asyncio
import contextvars
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Callable, TypeVar

from .embedding_store import EmbeddingBusy

T = TypeVar("T")
_PRIORITY = contextvars.ContextVar("embedding_priority", default="background")
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="integral-embedding")


def bounded_setting(name: str, default: int, minimum: int, maximum: int) -> int:
    """Read bounded operational limits without permitting unlimited queues."""
    try:
        return min(maximum, max(minimum, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


@contextmanager
def interactive_encoding():
    """Prioritize a query without changing provider/model call signatures."""
    token = _PRIORITY.set("query")
    try:
        yield
    finally:
        _PRIORITY.reset(token)


def is_interactive() -> bool:
    """Identify query encoding without changing provider call signatures."""
    return _PRIORITY.get() == "query"


class _Admission:
    def __init__(self):
        self.condition = asyncio.Condition()
        self.running = False
        self.waiting = 0
        self.queries = 0
        self.query_streak = 0
        self.peak_waiting = 0
        self.query_wait_s = 0.0
        self.background_wait_s = 0.0

    def _record_wait(self, query: bool, waited: float) -> None:
        if query:
            self.query_wait_s = max(self.query_wait_s, waited)
        else:
            self.background_wait_s = max(self.background_wait_s, waited)

    async def run(self, operation: Callable[[], T]) -> T:
        query = is_interactive()
        async with self.condition:
            limit = bounded_setting("EMBEDDING_QUEUE_LIMIT", 32, 1, 256)
            if self.waiting >= limit:
                raise EmbeddingBusy("Search encoding is busy; retry shortly")
            self.waiting += 1
            self.peak_waiting = max(self.peak_waiting, self.waiting)
            self.queries += int(query)
            try:
                # Background work receives a slice after eight queries. Query
                # priority applies between batches, never preempts native work.
                def ready():
                    background = self.waiting - self.queries
                    turn = (query and (self.query_streak < 8 or not background)) or (
                        not query and (not self.queries or self.query_streak >= 8)
                    )
                    return not self.running and turn

                timeout = (
                    bounded_setting("EMBEDDING_QUEUE_TIMEOUT_MS", 500, 10, 5000) / 1000
                )
                waited_from = time.perf_counter()
                try:
                    async with asyncio.timeout(timeout if query else 30):
                        await self.condition.wait_for(ready)
                except TimeoutError as exc:
                    self._record_wait(query, time.perf_counter() - waited_from)
                    raise EmbeddingBusy("Search encoding queue timed out") from exc
                self._record_wait(query, time.perf_counter() - waited_from)
                self.running = True
                self.query_streak = self.query_streak + 1 if query else 0
            finally:
                self.waiting -= 1
                self.queries -= int(query)
                self.condition.notify_all()
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(_EXECUTOR, operation)

        async def release():
            async with self.condition:
                self.running = False
                self.condition.notify_all()

        # Shield the native future so cancellation does not release admission
        # while the encoder is still consuming CPU/memory.
        try:
            return await asyncio.shield(future)
        finally:
            if future.done():
                await release()
            else:

                def finished(result):
                    # Consume late failures rather than orphaning diagnostics.
                    if not result.cancelled():
                        result.exception()
                    if not loop.is_closed():
                        loop.create_task(release())

                future.add_done_callback(finished)


async def run_encoding(operation: Callable[[], T]) -> T:
    """Execute at most one local native batch with bounded waiting per process."""
    loop = asyncio.get_running_loop()
    state = getattr(loop, "_integral_embedding_admission", None)
    if state is None:
        state = _Admission()
        loop._integral_embedding_admission = state  # type: ignore[attr-defined]
    return await state.run(operation)
