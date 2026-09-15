"""Per-principal sliding-window limits for speech endpoints.

In-process on purpose: Integral runs one API worker (ADR-005), and the
IP-keyed ``RateLimitMiddleware`` bucket can't express "per user". Under
several replicas each keeps its own window, so the effective limit
multiplies by the replica count.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, Tuple


def parse_rate(spec: str) -> Tuple[int, float]:
    """``"20/60"`` → ``(20, 60.0)``: at most 20 hits per 60 seconds."""
    count, _, window = (spec or "").partition("/")
    try:
        limit, seconds = int(count), float(window)
    except ValueError as exc:
        raise ValueError(
            f"invalid rate spec {spec!r}; expected '<count>/<seconds>'"
        ) from exc
    if limit < 1 or seconds <= 0:
        raise ValueError(f"invalid rate spec {spec!r}; both parts must be positive")
    return limit, seconds


class SlidingWindowLimiter:
    """At most ``limit`` hits per key inside any ``window_seconds`` span."""

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> bool:
        """Record a hit for ``key``; False (and nothing recorded) when over."""
        now = self._clock()
        cutoff = now - self._window
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self._limit:
                return False
            hits.append(now)
            return True


_LIMITERS: Dict[Tuple[str, str], SlidingWindowLimiter] = {}


def allow(bucket: str, principal: str, spec: str) -> bool:
    """Count one ``bucket`` hit for ``principal`` against ``spec``."""
    key = (bucket, spec)
    limiter = _LIMITERS.get(key)
    if limiter is None:
        limit, window = parse_rate(spec)
        limiter = _LIMITERS.setdefault(key, SlidingWindowLimiter(limit, window))
    return limiter.hit(principal)


def reset_rate_limits() -> None:
    """Test helper — drop every window."""
    _LIMITERS.clear()
