"""Opt-in populated PostgreSQL picker qualification; writes only test fixtures.

Use INTEGRAL_RUN_SLOW_TESTS=1 INTEGRAL_TEST_DB=postgres and a disposable DB.
PERF_REPORT selects a local JSON report; errors never become latency successes.
"""

import asyncio
import importlib.metadata
import json
import logging
import math
import os
import platform
import time
from pathlib import Path

import pytest

from tests.test_relation_options import _source

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.postgres,
    pytest.mark.slow,
    pytest.mark.skipif(
        os.getenv("INTEGRAL_TEST_DB") != "postgres"
        or not os.getenv("INTEGRAL_RUN_SLOW_TESTS"),
        reason="Opt-in isolated PostgreSQL benchmark",
    ),
]


@pytest.mark.parametrize("size", [100, 1000, 10000])
async def test_picker_scale(
    authenticated_client, test_user, size, monkeypatch, request
):
    import jvspatial

    # Synthetic throughput qualification measures the data path, not the
    # deliberately protective 60/minute default. Never change live settings.
    from jvspatial.api.middleware.rate_limit import RateLimitMiddleware
    from jvspatial.db import get_prime_database

    from app.models.nodes import Entry, Track

    async def allow_fixture_only(self, *args, **kwargs):
        return False

    monkeypatch.setattr(RateLimitMiddleware, "_is_rate_limited", allow_fixture_only)
    source = await _source(authenticated_client)
    track = await Track.get(source["track_id"])
    ctx = await track.get_context()
    await ctx.ensure_indexes(Entry)
    db = get_prime_database()
    timings = {}
    sample_count = int(os.getenv("PERF_SAMPLES", "20"))
    assert 1 <= sample_count <= 100, "PERF_SAMPLES must be between 1 and 100"

    class RequestTimings(logging.Handler):
        def emit(self, record):
            payload = getattr(record, "perf", {})
            if "route" in payload and payload.get("trace_id"):
                timings[payload["trace_id"]] = payload

    logger = logging.getLogger("perf.trace")
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    request.addfinalizer(lambda: logger.setLevel(previous_level))
    handler = RequestTimings()
    logger.addHandler(handler)
    request.addfinalizer(lambda: logger.removeHandler(handler))
    timestamp = "2026-01-01T00:00:00+00:00"
    rows = [
        {
            "id": f"n.Entry.perf-{size}-{i:06d}",
            "entity": "Entry",
            "context": {
                "title": f"Synthetic option {i:06d}",
                "track_id": track.id,
                "type_id": source["entry_type_id"],
                "visibility": "inherit",
                "body": "",
                "author_id": "",
                "tags": [],
                "custom_fields": {},
                "created_at": timestamp,
                "updated_at": timestamp,
                "status": "active",
            },
        }
        for i in range(size)
    ]
    edges = []
    for i, row in enumerate(rows):
        edges.extend(
            [
                {
                    "id": f"e.CONTAINS.perf-{size}-{i}",
                    "entity": "CONTAINS",
                    "source": track.id,
                    "target": row["id"],
                    "context": {},
                    "bidirectional": False,
                },
                {
                    "id": f"e.IS_OF_TYPE.perf-{size}-{i}",
                    "entity": "IS_OF_TYPE",
                    "source": row["id"],
                    "target": source["entry_type_id"],
                    "context": {},
                    "bidirectional": False,
                },
            ]
        )
    assert await db.bulk_save("node", rows) == size
    assert await db.bulk_save("edge", edges) == size * 2
    inner = db
    while hasattr(inner, "inner"):
        inner = inner.inner
    assert inner.schema_name.isidentifier()
    async with inner._acquire_conn() as connection:
        await connection.execute(f"ANALYZE {inner.schema_name}.node")
        await connection.execute(f"ANALYZE {inner.schema_name}.edge")
    versions = {}
    for name in [
        "jvspatial",
        "integral-core",
        "integral-sdk",
        "opentelemetry-sdk",
        "asyncpg",
    ]:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    report = {
        "size": size,
        "jvspatial_path": jvspatial.__file__,
        "selected_jvspatial_version": jvspatial.__version__,
        "versions": versions,
        "host": {
            "architecture": platform.machine(),
            "os": platform.system(),
            "logical_cpus": os.cpu_count(),
        },
        "database": {
            "adapter": type(inner).__name__,
            "pool_min": inner.min_size,
            "pool_max": inner.max_size,
            "pooler_mode": inner.pooler_mode,
            "command_timeout": inner.command_timeout,
        },
        "cells": [],
    }
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # Compare the actual old picker request shape with its compact replacement.
    for tracing in [False, True]:
        monkeypatch.setattr(
            trace,
            "_TRACER_PROVIDER",
            provider if tracing else trace.NoOpTracerProvider(),
        )
        monkeypatch.setenv("INTEGRAL_PERF_TRACE", "1" if tracing else "0")
        monkeypatch.setenv("INTEGRAL_PERF_HEADER_ENABLED", "1")
        for path, params in [
            ("/api/entries", {"track_id": track.id, "limit": 200}),
            ("/api/relation-options", source),
        ]:
            for concurrency in [1, 10]:
                semaphore = asyncio.Semaphore(concurrency)

                async def sample(index):
                    async with semaphore:
                        began = time.perf_counter()
                        response = await authenticated_client.get(path, params=params)
                        elapsed = (time.perf_counter() - began) * 1000
                        assert response.status_code == 200, response.text
                        body = response.json()
                        options = body.get("options", body.get("entries", []))
                        assert len(options) == min(
                            size, 50 if path.endswith("relation-options") else 100
                        )
                        return {
                            "ms": elapsed,
                            "ops": int(response.headers["x-db-round-trip-count"]),
                            "bytes": len(response.content),
                            "status": response.status_code,
                            "timing": timings.pop(
                                response.headers.get("x-trace-id"), None
                            ),
                        }

                cold = await sample(0)
                samples = await asyncio.gather(
                    *(sample(i) for i in range(sample_count))
                )
                ordered = sorted(s["ms"] for s in samples)
                report["cells"].append(
                    {
                        "path": path,
                        "concurrency": concurrency,
                        "tracing": tracing,
                        "cold": cold,
                        "samples": samples,
                        "p50_ms": ordered[math.ceil(len(ordered) * 0.5) - 1],
                        "p95_ms": ordered[math.ceil(len(ordered) * 0.95) - 1],
                    }
                )
    if size == 10000:
        from tests.picker_sql_probe import ParameterizedCandidateProbe

        inner = db
        while hasattr(inner, "inner"):
            inner = inner.inner
        probe = ParameterizedCandidateProbe(inner)
        original = inner.find
        monkeypatch.setenv("INTEGRAL_PERF_TRACE", "0")
        monkeypatch.setattr(trace, "_TRACER_PROVIDER", trace.NoOpTracerProvider())
        path, params = "/api/relation-options", source
        expected = (await authenticated_client.get(path, params=params)).json()[
            "options"
        ]
        for reader in ["jvspatial", "parameterized_postgres"]:
            monkeypatch.setattr(
                inner, "find", original if reader == "jvspatial" else probe.find
            )
            for concurrency in [1, 10]:
                semaphore = asyncio.Semaphore(concurrency)
                samples = await asyncio.gather(
                    *(sample(i) for i in range(sample_count))
                )
                ordered = sorted(item["ms"] for item in samples)
                actual = (await authenticated_client.get(path, params=params)).json()[
                    "options"
                ]
                assert actual == expected
                report["cells"].append(
                    {
                        "path": path,
                        "reader": reader,
                        "concurrency": concurrency,
                        "tracing": False,
                        "samples": samples,
                        "p50_ms": ordered[math.ceil(len(ordered) * 0.5) - 1],
                        "p95_ms": ordered[math.ceil(len(ordered) * 0.95) - 1],
                    }
                )
        report["candidate_probe_calls"] = probe.calls
        report["candidate_plan"] = await probe.explain()
        monkeypatch.setattr(inner, "find", original)
    # Search must reach a choice outside the old 200-item client preload.
    response = await authenticated_client.get(
        "/api/relation-options",
        params={**source, "q": f"Synthetic option {size - 1:06d}"},
    )
    assert [r["id"] for r in response.json()["options"]] == [rows[-1]["id"]]
    if size >= 1000:
        cursor = None
        identities = []
        for _ in range(5):
            response = await authenticated_client.get(
                "/api/relation-options",
                params={**source, **({"cursor": cursor} if cursor else {})},
            )
            assert response.status_code == 200, response.text
            page = response.json()
            identities.extend(item["id"] for item in page["options"])
            cursor = page["next_cursor"]
        assert identities == [row["id"] for row in rows[:250]]
        report["pagination_verified_past_200"] = True
    if size == 10000:
        # Populating another valid workspace must not add admission/hydration
        # work to this page. This complements the physical SQL plan comparison.
        from app.services.permissions import get_user_node
        from app.services.resource_location import locate_resource
        from tests.fixtures.workspaces import make_org_workspace

        origin = await locate_resource("track", track.id)
        headers = {"X-Integral-Scope": f"ws:{origin.workspace_id}"}
        before = await authenticated_client.get(
            "/api/relation-options", params=source, headers=headers
        )
        other = await make_org_workspace(
            "Unrelated picker workspace", owner=await get_user_node(test_user.id)
        )
        created = await authenticated_client.post(
            "/api/tracks",
            json={"title": "Unrelated records", "visibility": "private"},
            headers={"X-Integral-Scope": f"ws:{other.id}"},
        )
        assert created.status_code == 200, created.text
        unrelated_track = created.json()["track"]["id"]
        created_type = await authenticated_client.post(
            "/api/entry-types",
            json={"track_id": unrelated_track, "name": "Unrelated record"},
            headers={"X-Integral-Scope": f"ws:{other.id}"},
        )
        assert created_type.status_code == 200, created_type.text
        unrelated_type = created_type.json()["entry_type"]["id"]
        unrelated = [
            {
                **row,
                "id": f"n.Entry.unrelated-{i:06d}",
                "context": {
                    **row["context"],
                    "track_id": unrelated_track,
                    "type_id": unrelated_type,
                },
            }
            for i, row in enumerate(rows)
        ]
        assert await db.bulk_save("node", unrelated) == size
        assert (
            await db.bulk_save(
                "edge",
                [
                    {
                        "id": f"e.{kind}.unrelated-{i}",
                        "entity": kind,
                        "source": source_id,
                        "target": target_id,
                        "context": {},
                        "bidirectional": False,
                    }
                    for i, row in enumerate(unrelated)
                    for kind, source_id, target_id in [
                        ("CONTAINS", unrelated_track, row["id"]),
                        ("IS_OF_TYPE", row["id"], unrelated_type),
                    ]
                ],
            )
            == 2 * size
        )
        after = await authenticated_client.get(
            "/api/relation-options", params=source, headers=headers
        )
        assert before.status_code == after.status_code == 200
        assert before.json()["options"] == after.json()["options"]
        counts = [int(r.headers["x-db-round-trip-count"]) for r in [before, after]]
        assert counts[1] <= counts[0] + 2, f"Unrelated data grew picker work: {counts}"
        report["unrelated_workspace_budget"] = {
            "records": size,
            "before_operations": counts[0],
            "after_operations": counts[1],
        }
    spans = exporter.get_finished_spans()
    allowed_attributes = {
        "http.request.method",
        "http.route",
        "http.response.status_code",
        "db.system",
        "db.operation",
        "db.collection.name",
        "db.success",
        "db.result_count",
        "db.pool_wait_ms",
        "db.driver_statements",
        "db.driver_duration_ms",
        "db.decoded_bytes",
        "db.decoded_chars",
        "db.execution_mode",
        "db.partial_pushdown",
        "db.scanned_rows",
        "error.type",
        "result.count",
        "walker.class",
        "walker.step_count",
        "walker.paused",
    }
    report["recorded_spans"] = len(spans)
    report["spans"] = [
        {
            "name": span.name,
            "trace_id": f"{span.context.trace_id:032x}",
            "span_id": f"{span.context.span_id:016x}",
            "parent_span_id": f"{span.parent.span_id:016x}" if span.parent else None,
            "started_ns": span.start_time,
            "finished_ns": span.end_time,
            "attributes": {
                key: value
                for key, value in span.attributes.items()
                if key in allowed_attributes
            },
            "status": span.status.status_code.name,
        }
        for span in spans
    ]
    provider.shutdown()
    logger.removeHandler(handler)
    output = Path(os.getenv("PERF_REPORT", "/tmp/integral-picker-performance"))
    output.mkdir(parents=True, exist_ok=True)
    (output / f"{size}.json").write_text(json.dumps(report, indent=2))
    print(
        json.dumps({k: v for k, v in report.items() if k not in {"cells", "spans"}}),
        flush=True,
    )
    for cell in report["cells"]:
        print({k: v for k, v in cell.items() if k != "samples"}, flush=True)
