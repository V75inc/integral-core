"""Opt-in PostgreSQL evidence for 50k Entry query and aggregate paths.

Run explicitly with::

    INTEGRAL_TEST_DB=postgres INTEGRAL_RUN_SLOW_TESTS=1 \
      .venv/bin/pytest tests/test_scale_query_benchmark.py -s

The fixture uses a private test user, App, Track, and database rows. It reports
the p95 of the same query with and without exact scalar-filter pushdown; the
comparison is diagnostic, while row identity and completeness are assertions.
"""

from __future__ import annotations

import os
import statistics
import time
import uuid

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres, pytest.mark.slow]


@pytest.mark.skipif(
    (os.getenv("INTEGRAL_TEST_DB") or "json").lower() not in ("postgres", "postgresql"),
    reason="50k scale evidence requires isolated PostgreSQL test DB",
)
@pytest.mark.skipif(
    not os.getenv("INTEGRAL_RUN_SLOW_TESTS"),
    reason="50k scale evidence is opt-in",
)
async def test_query_entries_50k_scalar_filter_p95(monkeypatch):
    from jvspatial.api.auth.models import UserCreate
    from jvspatial.db import get_prime_database

    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.apps import create_app
    from app.api.auth import _get_auth_service
    from app.api.tracks import create_track
    from app.models.nodes import User
    from app.services import permissions
    from app.services.app_graph import catalog_user
    from app.services.personal_workspace import ensure_personal_workspace

    run_id = uuid.uuid4().hex[:12]
    auth = await _get_auth_service().register_user(
        UserCreate(email=f"scale-{run_id}@example.com", password="scale-test-pass")
    )
    user = await User.create(user_id=auth.id, display_name="Scale fixture")
    await catalog_user(user)
    workspace = await ensure_personal_workspace(user)
    app_result = await invoke_route_in_process(
        create_app,
        principal_id=auth.id,
        scope=workspace.id,
        name=f"Scale fixture {run_id}",
    )
    app_id = (app_result.get("app") or app_result.get("space") or {}).get("id")
    track_result = await invoke_route_in_process(
        create_track,
        principal_id=auth.id,
        scope=workspace.id,
        title=f"Scale track {run_id}",
        visibility="private",
        app_id=app_id,
    )
    track_id = track_result["track"]["id"]

    database = get_prime_database()
    inner = database
    for _ in range(4):
        nested = getattr(inner, "inner", None) or getattr(inner, "_db", None)
        if nested is None:
            break
        inner = nested
    if not hasattr(inner, "bulk_save") or not hasattr(inner, "_acquire_conn"):
        pytest.skip("test database is not the PostgreSQL backend")

    track = await __import__("app.models.nodes", fromlist=["Track"]).Track.get(track_id)
    context = await track.get_context()
    node_collection = context._get_collection_name("n")
    edge_collection = context._get_collection_name("e")
    schema = inner.schema_name
    ids = [f"n.Entry.scale-{run_id}-{i:05d}" for i in range(50_000)]
    match_id = ids[-1]
    timestamp = "2026-01-01T00:00:00+00:00"
    node_rows = [
        {
            "id": entry_id,
            "entity": "Entry",
            "context": {
                "title": "Selective match" if entry_id == match_id else "Background",
                "author_id": auth.id,
                "track_id": track_id,
                "type_id": "",
                "tags": [],
                "custom_fields": {},
                "status": "active",
                "body": "",
                "visibility": "inherit",
                "created_at": timestamp,
                "updated_at": timestamp,
            },
        }
        for entry_id in ids
    ]
    edge_rows = [
        {
            "id": f"e.CONTAINS.scale-{run_id}-{i:05d}",
            "entity": "CONTAINS",
            "source": track_id,
            "target": entry_id,
            "context": {"added_at": timestamp, "position": None},
            "bidirectional": False,
        }
        for i, entry_id in enumerate(ids)
    ]

    try:
        assert await database.bulk_save(node_collection, node_rows) == len(node_rows)
        assert await database.bulk_save(edge_collection, edge_rows) == len(edge_rows)

        original = permissions.get_user_accessible_entries

        async def without_candidate_pushdown(user_id, requested_track_id, **kwargs):
            kwargs.pop("candidate_query", None)
            return await original(user_id, requested_track_id, **kwargs)

        async def measure(repetitions: int) -> list[float]:
            values = []
            for _ in range(repetitions):
                started = time.perf_counter()
                result = await query_entries(
                    user_id=auth.id,
                    track_id=track_id,
                    filters=[
                        {"field": "title", "op": "eq", "value": "Selective match"}
                    ],
                    limit=20,
                )
                values.append((time.perf_counter() - started) * 1000)
                assert result["total"] == 1
                assert [row["id"] for row in result["entries"]] == [match_id]
            return values

        from app.services.agent_insights import (
            activity_digest,
            count_entries_grouped,
            query_entries,
        )

        # Warm both query plans, then compare 20 runs so p95 is meaningful.
        await measure(1)
        pushed = await measure(20)
        monkeypatch.setattr(
            "app.services.permissions.get_user_accessible_entries",
            without_candidate_pushdown,
        )
        await measure(1)
        baseline = await measure(20)

        pushed_p95 = sorted(pushed)[18]
        baseline_p95 = sorted(baseline)[18]
        print(
            "\n50k query_entries p95 (ms): "
            f"persistence pushdown={pushed_p95:.1f}, "
            f"candidate hydration={baseline_p95:.1f}, "
            f"median pushed={statistics.median(pushed):.1f}"
        )
        assert pushed_p95 <= 250, (
            "selective scalar-filter query exceeded the frozen 250ms p95 budget: "
            f"{pushed_p95:.1f}ms"
        )

        async def measure_operation(operation, repetitions: int = 20) -> list[float]:
            values = []
            for _ in range(repetitions):
                started = time.perf_counter()
                result = await operation()
                values.append((time.perf_counter() - started) * 1000)
                assert result is not None
            return values

        plain = await measure_operation(
            lambda: query_entries(user_id=auth.id, track_id=track_id, limit=20)
        )
        keyword = await measure_operation(
            lambda: query_entries(
                user_id=auth.id,
                track_id=track_id,
                query="Selective match",
                limit=20,
            ),
            repetitions=5,
        )
        grouped = await measure_operation(
            lambda: count_entries_grouped(
                user_id=auth.id,
                group_by="track",
                workspace_id=workspace.id,
            )
        )
        digests = await measure_operation(
            lambda: activity_digest(
                user_id=auth.id,
                scope="track",
                scope_id=track_id,
                workspace_id=workspace.id,
            )
        )
        assert (await query_entries(user_id=auth.id, track_id=track_id, limit=20))[
            "total"
        ] == 50_000
        keyword_result = await query_entries(
            user_id=auth.id,
            track_id=track_id,
            query="Selective match",
            limit=20,
        )
        assert keyword_result["total"] == 1
        assert [row["id"] for row in keyword_result["entries"]] == [match_id]
        assert (
            await count_entries_grouped(
                user_id=auth.id, group_by="track", workspace_id=workspace.id
            )
        )["total_matched"] == 50_000
        assert (
            await activity_digest(
                user_id=auth.id,
                scope="track",
                scope_id=track_id,
                workspace_id=workspace.id,
            )
        )["total_entries"] == 50_000

        aggregate_p95 = {
            "unfiltered query": sorted(plain)[18],
            "grouped count": sorted(grouped)[18],
            "activity digest": sorted(digests)[18],
        }
        keyword_p95 = sorted(keyword)[4]
        print(
            "\n50k query surface p95 (ms): "
            + ", ".join(f"{name}={value:.1f}" for name, value in aggregate_p95.items())
        )
        print(f"50k bounded keyword query p95 (ms): {keyword_p95:.1f}")
        assert all(value <= 500 for value in aggregate_p95.values()), (
            "50k broad query/count/digest exceeded the 500ms p95 budget: "
            f"{aggregate_p95}"
        )
        assert keyword_p95 <= 2_000, (
            "50k bounded keyword query exceeded the 2,000ms p95 budget: "
            f"{keyword_p95:.1f}ms"
        )
    finally:
        async with inner._acquire_conn() as connection:
            await connection.execute(
                f"DELETE FROM {schema}.{edge_collection} "
                "WHERE data->>'source' = $1 AND data->>'entity' = 'CONTAINS' "
                "AND data->>'target' = ANY($2::text[])",
                track_id,
                ids,
            )
            await connection.execute(
                f"DELETE FROM {schema}.{node_collection} WHERE id = ANY($1::text[])",
                ids,
            )
