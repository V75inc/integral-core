"""Manual W3.7 baseline against the test Postgres database.

Run explicitly with ``INTEGRAL_TEST_DB=postgres pytest -s tests/load/bench_w37.py``.
This file is intentionally named ``bench_`` so the ordinary suite does not seed
50,000 records. It writes only to the per-run test database managed by conftest.
"""

from __future__ import annotations

import os
import statistics
import time
from datetime import datetime, timezone

import pytest
from jvspatial.core.context import get_default_context

from app.models.edges import CONTAINS, EXCLUDED_FROM
from app.models.nodes import Entry
from app.services.agent_insights import (
    activity_digest,
    count_entries_grouped,
    query_entries,
)


@pytest.mark.asyncio
async def test_w37_query_baseline(authenticated_client, test_user):
    count = int(os.environ.get("W37_SEED_COUNT", "50000"))
    samples = int(os.environ.get("W37_SAMPLES", "3"))
    app_response = await authenticated_client.post(
        "/api/apps", json={"name": "W37 Scale Fixture", "include_seed_data": False}
    )
    assert app_response.status_code == 200, app_response.text
    app = app_response.json()["app"]
    track_response = await authenticated_client.post(
        "/api/tracks",
        json={
            "title": "W37 Entries",
            "app_id": app["id"],
            "workspace_id": app["workspace_id"],
            "visibility": "private",
        },
    )
    assert track_response.status_code == 200, track_response.text
    track_id = track_response.json()["track"]["id"]
    principal_id = getattr(test_user, "user_id", None) or test_user.id
    ctx = get_default_context()
    created_at = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    first_entry_id = None
    for first in range(0, count, 1000):
        nodes = []
        edges = []
        for index in range(first, min(first + 1000, count)):
            entry = Entry(
                track_id=track_id,
                author_id=principal_id,
                title=f"W37 Asset {index:05d}",
                body="scale fixture",
                custom_fields={"value": index, "stage": "open"},
                created_at=created_at,
                updated_at=created_at,
            )
            if first_entry_id is None:
                first_entry_id = entry.id
            nodes.append(await entry.export())
            edges.append(await CONTAINS(source=track_id, target=entry.id).export())
        node_result = await ctx.database.bulk_save_detailed("node", nodes)
        edge_result = await ctx.database.bulk_save_detailed("edge", edges)
        assert node_result.saved == len(nodes), node_result
        assert edge_result.saved == len(edges), edge_result
    print(f"W37 seeded={count} seconds={time.perf_counter() - started:.3f}")
    excluded = os.environ.get("W37_EXCLUSION") == "1"
    if excluded:
        entry = await Entry.get(first_entry_id)
        await test_user.connect(entry, edge=EXCLUDED_FROM)

    durations = []
    for _ in range(samples):
        started = time.perf_counter()
        result = await query_entries(
            user_id=principal_id,
            track_id=track_id,
            workspace_id=app["workspace_id"],
            limit=20,
            sort_by="updated_at",
            sort_dir="desc",
        )
        durations.append(time.perf_counter() - started)
        assert result["total"] == count - int(excluded), result
        assert len(result["entries"]) == 20, result
    print(
        f"W37 query_entries seconds={durations} p95_estimate={max(durations):.3f} "
        f"median={statistics.median(durations):.3f}"
    )
    if os.environ.get("W37_ALL") == "1":
        started = time.perf_counter()
        grouped = await count_entries_grouped(
            user_id=principal_id,
            group_by="track",
            track_id=track_id,
            workspace_id=app["workspace_id"],
        )
        print(f"W37 count_entries seconds={time.perf_counter() - started:.3f}")
        assert grouped["total_matched"] == count - int(excluded), grouped
        started = time.perf_counter()
        digest = await activity_digest(
            user_id=principal_id,
            scope="track",
            scope_id=track_id,
            workspace_id=app["workspace_id"],
        )
        print(f"W37 activity_digest seconds={time.perf_counter() - started:.3f}")
        assert digest["total_entries"] == count - int(excluded), digest
