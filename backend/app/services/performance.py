"""Optional content-free tracing. The application owns the OpenTelemetry SDK."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("perf.trace")


def enabled() -> bool:
    return os.getenv("INTEGRAL_PERF_TRACE", "").lower() in {"1", "true", "yes"}


@dataclass
class RequestPerformance:
    phases: dict[str, float] = field(default_factory=dict)
    values: dict[str, float] = field(default_factory=dict)
    phase_counts: dict[str, int] = field(default_factory=dict)


current_performance: ContextVar[RequestPerformance | None] = ContextVar(
    "request_performance", default=None
)


def record_value(name: str, value: float) -> None:
    state = current_performance.get()
    if state is not None:
        state.values[name] = state.values.get(name, 0) + value


def initialize_tracing() -> bool:
    if not enabled():
        return False
    logger.setLevel(logging.INFO)
    endpoint = os.getenv("INTEGRAL_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(
            resource=Resource.create({"service.name": "integral-core"})
        )
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=endpoint, timeout=2),
                max_queue_size=2048,
                max_export_batch_size=128,
                schedule_delay_millis=1000,
            )
        )
        trace.set_tracer_provider(provider)
        return True
    except Exception:
        # Do not log exporter URLs, headers or exception messages.
        logger.warning("Performance exporter unavailable; local timings remain active")
        return False


@contextmanager
def trace_span(name: str, attributes: dict[str, Any] | None = None, *, context=None):
    """Only bounded, content-free attributes supplied by owning call sites."""
    if not enabled():
        yield None
        return
    try:
        from opentelemetry import trace

        manager = trace.get_tracer("integral.performance").start_as_current_span(
            name,
            context=context,
            attributes=attributes or {},
            record_exception=False,
            set_status_on_exception=False,
        )
    except Exception:
        manager = None
    if manager is None:
        yield None
        return
    with manager as span:
        try:
            yield span
        except BaseException as exc:
            span.set_attribute("error.type", type(exc).__name__)
            raise


def trace_id() -> str | None:
    try:
        from opentelemetry import trace

        context = trace.get_current_span().get_span_context()
        return f"{context.trace_id:032x}" if context.is_valid else None
    except Exception:
        return None


@contextmanager
def phase(name: str, **attributes):
    if not enabled():
        yield {}
        return
    began = time.perf_counter()
    values: dict = {}
    tool_name = attributes.get("tool_name")
    safe_attributes = (
        {"tool.name": tool_name}
        if isinstance(tool_name, str) and len(tool_name) <= 160
        else {}
    )
    with trace_span(name, safe_attributes) as span:
        try:
            yield values
        finally:
            elapsed = (time.perf_counter() - began) * 1000
            state = current_performance.get()
            if state is not None and (name in state.phases or len(state.phases) < 32):
                state.phases[name] = state.phases.get(name, 0) + elapsed
                state.phase_counts[name] = state.phase_counts.get(name, 0) + 1
            # Existing callers may supply identities. Export only numeric counts.
            counts = {
                k: v
                for k, v in values.items()
                if k == "count" and isinstance(v, (int, float))
            }
            if span is not None and "count" in counts:
                span.set_attribute("result.count", counts["count"])
            # The request summary already carries every bounded phase and
            # invocation count. Avoid formatting one log per permission call.
            if state is None:
                emit(
                    "perf.span",
                    {
                        "name": name,
                        "duration_ms": round(elapsed, 3),
                        "trace_id": trace_id(),
                        **counts,
                    },
                )


def capture_trace_context() -> str | None:
    if not enabled():
        return None
    try:
        from opentelemetry.propagate import inject

        carrier = {}
        inject(carrier)
        return carrier.get("traceparent")
    except Exception:
        return None


@contextmanager
def worker_trace(parent: str | None):
    if not enabled():
        yield None
        return
    context = None
    if enabled() and isinstance(parent, str) and len(parent) == 55:
        try:
            from opentelemetry.propagate import extract

            context = extract({"traceparent": parent})
        except Exception:
            pass
    state = RequestPerformance()
    token = current_performance.set(state)
    began = time.perf_counter()
    diagnostics = {"outcome": "completed"}
    try:
        with trace_span("work.execute", context=context) as span:
            try:
                yield diagnostics
            except BaseException as exc:
                from app.schemas.agentive.work import WorkError

                diagnostics["outcome"] = (
                    "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
                )
                if isinstance(exc, WorkError):
                    diagnostics["error_code"] = exc.code
                raise
            finally:
                from typing import get_args

                from app.schemas.agentive.work import WorkStatus

                outcome = diagnostics.get("outcome")
                if not isinstance(outcome, str) or outcome not in {
                    *get_args(WorkStatus),
                    "completed",
                    "error",
                }:
                    outcome = "completed"
                code = diagnostics.get("error_code")
                if span is not None:
                    span.set_attribute("work.status", outcome)
                    if outcome in {"failed", "expired", "dead_letter", "error"}:
                        from opentelemetry.trace import Status, StatusCode

                        span.set_status(Status(StatusCode.ERROR))
                    if (
                        isinstance(code, str)
                        and code.startswith("work.")
                        and len(code) <= 100
                    ):
                        span.set_attribute("work.error_code", code)
                emit(
                    "perf.work",
                    {
                        "outcome": outcome,
                        "trace_id": trace_id(),
                        "total_ms": round((time.perf_counter() - began) * 1000, 3),
                        "phases_ms": state.phases,
                        "phase_counts": state.phase_counts,
                        "values": state.values,
                    },
                )
    finally:
        current_performance.reset(token)


def emit(event: str, payload: dict) -> None:
    if not logger.isEnabledFor(logging.INFO):
        return
    logger.info(
        "%s %s",
        event,
        json.dumps(payload, separators=(",", ":")),
        extra={"perf": payload},
    )


def observe_interval(name: str, started, finished, attributes: dict[str, Any]) -> None:
    """Render content-free physical model observations with their real duration."""
    if not enabled() or finished is None:
        return
    try:
        from opentelemetry import trace

        began = int(started.timestamp() * 1_000_000_000)
        ended = max(began, int(finished.timestamp() * 1_000_000_000))
        span = trace.get_tracer("integral.performance").start_span(
            name, start_time=began, attributes=attributes
        )
        span.end(end_time=ended)
    except Exception:
        pass  # Diagnostic clocks/exporters cannot affect a model outcome.
