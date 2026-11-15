"""Lightweight perf instrumentation for hot service + API paths.

Spans emit a structured-log line `perf.span` carrying `name`, `duration_ms`,
optional `count`, and trace identity. Identity/content tags are discarded; opt-in
via env `INTEGRAL_PERF_TRACE=1`.

Usage:

    from app.services.perf_trace import perf_span

    async with perf_span("permissions.get_user_accessible_entries",
                        user_id=user_id, track_id=track_id):
        ...
"""

from __future__ import annotations

import functools
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, Iterator, TypeVar


def perf_enabled() -> bool:
    """Return whether ``INTEGRAL_PERF_TRACE`` opt-in spans are active."""
    from app.services.performance import enabled

    return enabled()


@asynccontextmanager
async def perf_span(name: str, **tags: Any) -> AsyncIterator[dict]:
    """Async perf span. Yields a mutable dict caller may set 'count' on."""
    from app.services.performance import phase

    with phase(name) as ctx:
        yield ctx


F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def perf_traced(name: str | None = None) -> Callable[[F], F]:
    """Decorator wrapper around perf_span for async functions."""

    def deco(fn: F) -> F:
        span_name = name or f"{fn.__module__}.{fn.__qualname__}"

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not perf_enabled():
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
    from app.services.performance import phase

    with phase(name) as ctx:
        yield ctx
