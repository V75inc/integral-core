"""Perf baseline helpers — query-count headers and edge-index verification.

Enable measurement in dev/CI:

    INTEGRAL_PERF_HEADER_ENABLED=1 INTEGRAL_PERF_TRACE=1 pytest tests/test_perf_baseline.py -v

Postgres edge-index check (optional):

    INTEGRAL_TEST_DB=postgres pytest tests/test_perf_baseline.py -v -k edge_index
"""

from __future__ import annotations

import os

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_perf_header_emits_db_round_trip_count(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """X-DB-Round-Trip-Count is present when INTEGRAL_PERF_HEADER_ENABLED=1."""
    monkeypatch.setenv("INTEGRAL_PERF_HEADER_ENABLED", "1")
    resp = await authenticated_client.get("/health")
    assert resp.status_code == 200
    assert "X-DB-Round-Trip-Count" in resp.headers
    count = int(resp.headers["X-DB-Round-Trip-Count"])
    assert count >= 0


@pytest.mark.asyncio
async def test_db_op_count_isolated_per_request(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """Each request gets a fresh holder — counts don't accumulate across requests.

    Counting runs through ``app.services.db_metrics.request_db_ops`` (a
    per-request mutable holder bound by PerfHeaderMiddleware), not the raw
    ``jvspatial.observability.db_op_counter`` int ContextVar, which cannot be
    read across BaseHTTPMiddleware task boundaries.
    """
    monkeypatch.setenv("INTEGRAL_PERF_HEADER_ENABLED", "1")
    first = await authenticated_client.get("/api/tracks")
    assert first.status_code == 200
    first_count = int(first.headers["X-DB-Round-Trip-Count"])
    assert first_count > 0, "authenticated list should hit the DB at least once"

    second = await authenticated_client.get("/api/tracks")
    assert second.status_code == 200
    second_count = int(second.headers["X-DB-Round-Trip-Count"])
    # A fresh holder per request: the second identical request reports its own
    # count, not first + second accumulated.
    assert (
        second_count < first_count * 2
    ), f"counts accumulate across requests: {first_count} then {second_count}"


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("INTEGRAL_TEST_DB", "json").lower() not in ("postgres", "postgresql"),
    reason="edge index EXPLAIN requires postgres test mode",
)
async def test_edge_source_index_used_by_planner():
    """EXPLAIN on a typical edge lookup should not seq-scan when indexes exist."""
    from jvspatial.db import get_prime_database

    db = get_prime_database()
    if not hasattr(db, "_acquire_conn"):
        pytest.skip("not postgres backend")

    schema = getattr(db, "schema_name", "public")
    sql = f"""
        EXPLAIN (FORMAT TEXT)
        SELECT data FROM {schema}.edge
        WHERE (data #>> '{{source}}') = $1
          AND entity = $2
        LIMIT 1
    """
    async with db._acquire_conn() as conn:
        rows = await conn.fetch(sql, "n.nonexistent", "CONTAINS")
    plan = "\n".join(r[0] for r in rows)
    assert (
        "Seq Scan" not in plan or "Index" in plan
    ), f"expected index-backed edge lookup, got plan:\n{plan}"
