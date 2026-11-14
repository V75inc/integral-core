"""Content-free diagnostics remain isolated across ASGI tasks and streams."""

import asyncio
import logging

import pytest

from app.middleware.performance import PerformanceMiddleware
from app.services.performance import current_performance, phase


@pytest.mark.asyncio
async def test_stream_completion_cancellation_and_request_isolation(
    monkeypatch, caplog
):
    monkeypatch.setenv("INTEGRAL_PERF_TRACE", "1")
    ready = asyncio.Event()

    async def app(scope, receive, send):
        with phase("test.phase"):
            await asyncio.sleep(0)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        if scope["path"] == "/cancel":
            ready.set()
            await asyncio.Event().wait()
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = PerformanceMiddleware(app)

    async def run(path):
        messages = []

        async def send(message):
            messages.append(message)

        await middleware(
            {"type": "http", "path": path, "method": "GET", "headers": []}, None, send
        )
        assert current_performance.get() is None
        return messages

    with caplog.at_level(logging.INFO, logger="perf.trace"):
        task = asyncio.create_task(run("/cancel"))
        await ready.wait()
        messages = await run("/complete")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert b"server-timing" in dict(messages[0]["headers"])
    records = [r.perf for r in caplog.records if r.message.startswith("perf.request ")]
    assert {r["outcome"] for r in records} == {"completed", "cancelled"}
    assert all(
        len(r["phases_ms"]) == 1 and r["total_ms"] >= r["response_start_ms"]
        for r in records
    )
    assert current_performance.get() is None


@pytest.mark.asyncio
async def test_disabled_tracing_emits_no_headers(monkeypatch):
    monkeypatch.delenv("INTEGRAL_PERF_TRACE", raising=False)
    messages = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})

    async def send(message):
        messages.append(message)

    await PerformanceMiddleware(app)({"type": "http"}, None, send)
    assert messages[0]["headers"] == []


def test_worker_timings_do_not_mutate_inherited_request_state(monkeypatch, caplog):
    from app.services.performance import RequestPerformance, record_value, worker_trace

    monkeypatch.setenv("INTEGRAL_PERF_TRACE", "1")
    outer = RequestPerformance()
    token = current_performance.set(outer)
    try:
        with caplog.at_level(logging.INFO, logger="perf.trace"):
            with worker_trace(None) as diagnostics:
                assert current_performance.get() is not outer
                record_value("db.logical_operations", 2)
                with phase("test.worker_phase"):
                    pass
                diagnostics["outcome"] = "failed"
        assert current_performance.get() is outer
        assert outer.values == {} and outer.phases == {}
        record = next(
            r.perf for r in caplog.records if r.message.startswith("perf.work ")
        )
        assert record["outcome"] == "failed"
        assert record["values"]["db.logical_operations"] == 2
        with pytest.raises(asyncio.CancelledError):
            with worker_trace(None):
                raise asyncio.CancelledError
        assert current_performance.get() is outer
    finally:
        current_performance.reset(token)


@pytest.mark.asyncio
async def test_trace_parent_survives_worker_boundary_without_content(monkeypatch):
    pytest.importorskip("opentelemetry.sdk.trace")
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from app.services.performance import capture_trace_context, trace_span, worker_trace

    monkeypatch.setenv("INTEGRAL_PERF_TRACE", "1")
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", provider)
    with trace_span("submit") as root:
        parent = capture_trace_context()
    with worker_trace(parent):
        with phase("tool.execute"):
            pass
    spans = exporter.get_finished_spans()
    worker = next(s for s in spans if s.name == "work.execute")
    assert worker.context.trace_id == root.get_span_context().trace_id
    assert worker.parent.span_id == root.get_span_context().span_id
    assert all(not s.events for s in spans)
    assert all(
        not any(key in str(s.attributes) for key in ["prompt", "token", "query"])
        for s in spans
    )
    provider.shutdown()


def test_exporter_setup_failure_keeps_app_available(monkeypatch):
    from app.services.performance import initialize_tracing

    monkeypatch.setenv("INTEGRAL_PERF_TRACE", "1")
    monkeypatch.setenv("INTEGRAL_OTLP_ENDPOINT", "http://127.0.0.1:1/v1/traces")
    import builtins

    original = builtins.__import__

    def failed(name, *args, **kwargs):
        if name.startswith("opentelemetry.exporter"):
            raise ImportError("absent optional dependency")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", failed)
    assert initialize_tracing() is False


@pytest.mark.asyncio
async def test_http_trace_continuity_and_concurrent_parent_isolation(monkeypatch):
    pytest.importorskip("opentelemetry.sdk.trace")
    from types import SimpleNamespace

    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", provider)
    monkeypatch.setenv("INTEGRAL_PERF_TRACE", "1")

    async def app(scope, receive, send):
        with phase("test.async_child"):
            await asyncio.sleep(0)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = PerformanceMiddleware(app)

    async def request(identity):
        messages = []

        async def send(message):
            messages.append(message)

        await middleware(
            {
                "type": "http",
                "method": "GET",
                "path": "/private-record-value",
                "route": SimpleNamespace(path="/records/{record_id}"),
                "headers": [
                    (b"traceparent", f"00-{identity}-1111111111111111-01".encode()),
                    (b"authorization", b"private-credential-value"),
                ],
            },
            None,
            send,
        )
        assert dict(messages[0]["headers"])[b"x-trace-id"].decode() == identity

    identities = ["a" * 32, "b" * 32]
    await asyncio.gather(*(request(identity) for identity in identities))
    spans = exporter.get_finished_spans()
    for identity in identities:
        same_trace = [s for s in spans if f"{s.context.trace_id:032x}" == identity]
        root = next(s for s in same_trace if s.name == "http.request")
        child = next(s for s in same_trace if s.name == "test.async_child")
        assert root.parent.span_id == int("1111111111111111", 16)
        assert child.parent.span_id == root.context.span_id
    assert "private-" not in str([(s.attributes, s.events) for s in spans])
    assert current_performance.get() is None
    provider.shutdown()


@pytest.mark.asyncio
async def test_exporter_runtime_failure_does_not_fail_response(monkeypatch):
    pytest.importorskip("opentelemetry.sdk.trace")
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter

    class UnavailableExporter(SpanExporter):
        def export(self, spans):
            raise ConnectionError("synthetic unavailable collector")

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(UnavailableExporter()))
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", provider)
    monkeypatch.setenv("INTEGRAL_PERF_TRACE", "1")
    messages = []

    async def app(scope, receive, send):
        with phase("test.exporter_failure"):
            pass
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def send(message):
        messages.append(message)

    await PerformanceMiddleware(app)({"type": "http", "method": "GET"}, None, send)
    assert messages[0]["status"] == 200
    assert messages[-1]["body"] == b"ok"
    assert current_performance.get() is None
    provider.shutdown()
