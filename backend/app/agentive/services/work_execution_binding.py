"""Task-owned server authority for in-process worker-to-route handoffs."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from app.schemas.agentive.work import WorkError, WorkExecutionContext

_active: ContextVar[tuple[object, WorkExecutionContext] | None] = ContextVar(
    "integral_bound_work_execution", default=None
)


@contextmanager
def bind_work_execution(context: WorkExecutionContext) -> Iterator[None]:
    """Bind immutable authority only for the current trusted worker task."""
    if not isinstance(context, WorkExecutionContext):
        raise TypeError("a trusted WorkExecutionContext is required")
    current = current_work_execution()
    if current is not None and current != context:
        raise WorkError("work.lease_lost", "Worker execution binding changed")
    token = _active.set((asyncio.current_task(), context))
    try:
        yield
    finally:
        _active.reset(token)


def current_work_execution() -> WorkExecutionContext | None:
    """Return the server binding, refusing authority inherited by another task."""
    active = _active.get()
    if active is None:
        return None
    if active[0] is not asyncio.current_task():
        raise WorkError("work.lease_lost", "Worker authority belongs to another task")
    return active[1]
