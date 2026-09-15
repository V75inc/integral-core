"""Per-request DB round-trip counting via the jvspatial MetricsRecorder seam.

Why not ``jvspatial.observability.db_op_counter`` alone: that ContextVar holds
an ``int`` and ``ObservableDatabase`` rebinds it (``set(get() + 1)``). Every
``BaseHTTPMiddleware`` in the stack (integral's and jvspatial's own auth /
rate-limit / error-logging layers) runs its downstream in a child anyio task
with a context COPY, so rebinding in the endpoint task never propagates back
to the middleware that wants to read the total.

This module counts through a *mutable holder* instead: the middleware binds a
fresh ``_RequestDBOps`` object into a ContextVar; child contexts inherit the
same object by reference, so ``ops += 1`` mutations made deep in the endpoint
task are visible to the outer middleware.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from typing import Any, Optional


def _slow_query_threshold_seconds() -> float:
    raw = os.environ.get("JVSPATIAL_SLOW_QUERY_MS", "")
    try:
        ms = float(raw) if raw != "" else 100.0
    except ValueError:
        ms = 100.0
    return ms / 1000.0


class _RequestDBOps:
    __slots__ = ("ops", "duration_seconds", "slow_ops")

    def __init__(self) -> None:
        self.ops = 0
        self.duration_seconds = 0.0
        self.slow_ops = 0


request_db_ops: ContextVar[Optional[_RequestDBOps]] = ContextVar(
    "integral_request_db_ops", default=None
)


class RequestDBOpRecorder:
    """jvspatial ``MetricsRecorder`` that counts ops into the request holder.

    Pass as ``create_database(..., observe=True, metrics=RequestDBOpRecorder())``.
    Implements the full protocol. Never raises (protocol requirement).
    """

    def record_duration(self, name: str, seconds: float, /, **labels: Any) -> None:
        if name != "jvspatial.db.op.duration_seconds":
            return
        holder = request_db_ops.get()
        if holder is None:
            return
        holder.duration_seconds += max(0.0, float(seconds))
        if float(seconds) >= _slow_query_threshold_seconds():
            holder.slow_ops += 1

    def increment_counter(
        self, name: str, /, *, amount: int = 1, **labels: Any
    ) -> None:
        holder = request_db_ops.get()
        if holder is None:
            return
        if name == "jvspatial.db.op.count":
            holder.ops += amount
        elif name == "jvspatial.db.op.slow_count":
            holder.slow_ops += amount

    def record_value(self, name: str, value: float, /, **labels: Any) -> None:
        return None


def install_request_op_recorder_on_prime_db() -> bool:
    """Best-effort: point the prime ObservableDatabase at our recorder.

    Covers the path where jvspatial's own ``DatabaseConfigurator`` built the
    prime DB (json/sqlite/mongodb server boot with
    ``JVSPATIAL_OBSERVABILITY_ENABLED=1``) — it wires a NullMetricsRecorder we
    cannot parameterize. Only replaces the recorder when it is the Null one,
    so an OpenTelemetry (or other real) backend configured upstream is never
    clobbered. Returns True when the recorder is in place.
    """
    try:
        from jvspatial.db import get_prime_database
        from jvspatial.db._observable import ObservableDatabase
        from jvspatial.observability.metrics import NullMetricsRecorder

        db = get_prime_database()
        if not isinstance(db, ObservableDatabase):
            return False
        if isinstance(db.metrics, RequestDBOpRecorder):
            return True
        if isinstance(db.metrics, NullMetricsRecorder):
            db.metrics = RequestDBOpRecorder()
            return True
        return False
    except Exception:
        return False
