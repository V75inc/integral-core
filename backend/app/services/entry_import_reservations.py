"""Task-local identity reservation for two-pass portable imports.

Only a new, already rooted shell may be supplied. The canonical entry creator
still validates fields, runs guards/hooks and emits its normal creation event.
No HTTP request can select an existing identity through this seam.
"""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar

from app.models.nodes import Entry

_reservation: ContextVar[tuple | None] = ContextVar(
    "portable_entry_reservation", default=None
)


@contextmanager
def reserved_import_entry(entry: Entry, user_id: str):
    """Bind one newly allocated shell to the current creator call."""
    token = _reservation.set((asyncio.current_task(), entry, user_id))
    try:
        yield
    finally:
        _reservation.reset(token)


def consume_import_entry(*, track_id: str, user_id: str, type_id: str) -> Entry | None:
    """Consume a reservation once, checking task and destination identity."""
    pending = _reservation.get()
    if pending is None:
        return None
    task, entry, principal = pending
    if (
        task is not asyncio.current_task()
        or principal != user_id
        or entry.track_id != track_id
        or entry.type_id != type_id
        or entry.status != "importing"
    ):
        raise RuntimeError("Invalid portable entry reservation")
    _reservation.set(None)
    return entry
