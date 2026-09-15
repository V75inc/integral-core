"""Optional response header with per-request DB round-trip count."""

from __future__ import annotations

import os
from typing import Any, Awaitable, Callable, Dict

from jvspatial.core.context import begin_request_identity_map, end_request_identity_map

from app.services.db_metrics import (
    _RequestDBOps,
    install_request_op_recorder_on_prime_db,
    request_db_ops,
)


def _enabled() -> bool:
    raw = str(os.getenv("INTEGRAL_PERF_HEADER_ENABLED", "0")).lower()
    return raw in ("1", "true", "yes", "on")


class PerfHeaderMiddleware:
    """Emit ``X-DB-Round-Trip-Count`` when perf headers are enabled.

    Pure ASGI middleware. Counting goes through the mutable holder in
    ``app.services.db_metrics`` (see that module for why the int-valued
    ``jvspatial.observability.db_op_counter`` cannot be read across the
    ``BaseHTTPMiddleware`` task boundaries in this stack).
    """

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Dict[str, Any],
        receive: Callable[[], Awaitable[Dict[str, Any]]],
        send: Callable[[Dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        enabled = _enabled()
        if enabled:
            # Prime DB may have been built by jvspatial's DatabaseConfigurator
            # with a Null recorder; swap in the counting one (idempotent).
            install_request_op_recorder_on_prime_db()

        holder = _RequestDBOps()
        ops_token = request_db_ops.set(holder)
        imap_token = begin_request_identity_map()

        async def send_with_header(message: Dict[str, Any]) -> None:
            if message.get("type") == "http.response.start" and enabled:
                headers = list(message.get("headers") or [])
                headers.append(
                    (b"x-db-round-trip-count", str(holder.ops).encode("latin-1"))
                )
                if holder.slow_ops:
                    headers.append(
                        (
                            b"x-db-slow-query-count",
                            str(holder.slow_ops).encode("latin-1"),
                        )
                    )
                if holder.duration_seconds > 0:
                    headers.append(
                        (
                            b"x-db-duration-ms",
                            f"{holder.duration_seconds * 1000:.1f}".encode("latin-1"),
                        )
                    )
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            end_request_identity_map(imap_token)
            request_db_ops.reset(ops_token)
