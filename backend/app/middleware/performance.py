"""Pure ASGI performance boundary, including response body/stream completion."""

import asyncio
import logging
import time

from app.services.performance import (
    RequestPerformance,
    current_performance,
    emit,
    enabled,
    trace_id,
    trace_span,
)

logger = logging.getLogger("perf.trace")


class PerformanceMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not enabled():
            return await self.app(scope, receive, send)
        context = None
        try:
            from opentelemetry.propagate import extract

            # Only trace context; no incoming baggage or arbitrary header capture.
            headers = {
                k.decode("latin-1").lower(): v.decode("latin-1")
                for k, v in scope.get("headers", [])
                if k.lower() in {b"traceparent", b"tracestate"}
            }
            context = extract(headers)
        except Exception:
            pass
        state = RequestPerformance()
        token = current_performance.set(state)
        began = time.perf_counter()
        response_ms = None
        response_complete_ms = None
        status = None
        outcome = "completed"

        async def timed_send(message):
            nonlocal response_ms, response_complete_ms, status
            if message["type"] == "http.response.start":
                response_ms = (time.perf_counter() - began) * 1000
                status = message["status"]
                headers = list(message.get("headers", []))
                headers.append(
                    (b"server-timing", f"app;dur={response_ms:.2f}".encode())
                )
                identity = trace_id()
                if identity:
                    headers.append((b"x-trace-id", identity.encode()))
                message = {**message, "headers": headers}
            await send(message)
            if message["type"] == "http.response.body" and not message.get(
                "more_body", False
            ):
                response_complete_ms = (time.perf_counter() - began) * 1000

        try:
            with trace_span(
                "http.request",
                {"http.request.method": scope.get("method", "")},
                context=context,
            ) as span:
                try:
                    await self.app(scope, receive, timed_send)
                except asyncio.CancelledError:
                    outcome = "cancelled"
                    raise
                except BaseException:
                    outcome = "error"
                    raise
                finally:
                    if outcome == "completed":
                        if response_complete_ms is None:
                            outcome = "incomplete"
                        elif status is not None and status >= 400:
                            outcome = "http_error"
                    route = scope.get("route")
                    route_name = getattr(route, "path", "unmatched")
                    if span is not None:
                        span.set_attribute("http.route", route_name)
                        if status is not None:
                            span.set_attribute("http.response.status_code", status)
                    emit(
                        "perf.request",
                        {
                            "route": route_name,
                            "status": status,
                            "outcome": outcome,
                            "trace_id": trace_id(),
                            "response_start_ms": response_ms,
                            "response_complete_ms": response_complete_ms,
                            "total_ms": round((time.perf_counter() - began) * 1000, 3),
                            "phases_ms": state.phases,
                            "phase_counts": state.phase_counts,
                            "values": state.values,
                        },
                    )
        finally:
            current_performance.reset(token)
