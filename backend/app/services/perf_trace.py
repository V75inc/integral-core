"""Lightweight perf instrumentation for hot service + API paths.

Spans emit a structured-log line `perf.span` carrying `name`, `duration_ms`,
optional `count`, and arbitrary tagged context. No external dependencies; opt-in
via env `INTEGRAL_PERF_TRACE=1`.

Usage:

    from app.services.perf_trace import perf_span

    async with perf_span("permissions.get_user_accessible_entries",
                        user_id=user_id, track_id=track_id):
        ...
"""

from __future__ import annotations

import functools
import logging
import os
import time
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, Iterator, TypeVar

logger = logging.getLogger("perf.trace")

_ENABLED = os.environ.get("INTEGRAL_PERF_TRACE", "").lower() in ("1", "true", "yes")


def perf_enabled() -> bool:
    """Return whether ``INTEGRAL_PERF_TRACE`` opt-in spans are active."""
    return _ENABLED


@asynccontextmanager
async def perf_span(name: str, **tags: Any) -> AsyncIterator[dict]:
    """Async perf span. Yields a mutable dict caller may set 'count' on."""
    if not _ENABLED:
        yield {}
        return
    started = time.perf_counter()
    ctx: dict = {}
    try:
        yield ctx
    finally:
        duration_ms = (time.perf_counter() - started) * 1000.0
        payload = {"name": name, "duration_ms": round(duration_ms, 3), **tags, **ctx}
        logger.info("perf.span", extra={"perf": payload})


F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def perf_traced(name: str | None = None) -> Callable[[F], F]:
    """Decorator wrapper around perf_span for async functions."""

    def deco(fn: F) -> F:
        span_name = name or f"{fn.__module__}.{fn.__qualname__}"

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not _ENABLED:
                return await fn(*args, **kwargs)
            async with perf_span(span_name) as ctx:
                result = await fn(*args, **kwargs)
                try:
                    ctx["count"] = len(result)  # type: ignore[arg-type]
                except TypeError:
                    pass
                return result

        return wrapper  # type: ignore[return-value]

    return deco


@contextmanager
def perf_span_sync(name: str, **tags: Any) -> Iterator[dict]:
    """Sync variant for non-async hot paths."""
    if not _ENABLED:
        yield {}
        return
    started = time.perf_counter()
    ctx: dict = {}
    try:
        yield ctx
    finally:
        duration_ms = (time.perf_counter() - started) * 1000.0
        payload = {"name": name, "duration_ms": round(duration_ms, 3), **tags, **ctx}
        logger.info("perf.span", extra={"perf": payload})
