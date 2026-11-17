"""Authenticated dashboard/App reads must not scan unrelated Track inventories."""

import json
import logging
import os
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.getenv("INTEGRAL_TEST_DB", "json") not in ("postgres", "postgresql"),
        reason="1,000-Track query budget requires disposable PostgreSQL",
    ),
]


async def test_dashboard_exact_tracks_ignore_unrelated_inventory(
    authenticated_client, monkeypatch, caplog
):
    from app.api import apps as apps_api
    from app.models.edges import CATALOGS
    from app.models.nodes import OperationalModel, Track
    from app.services import agent_insights, dashboard_service

    sources = {
        "query": str(Path(agent_insights.__file__).resolve()),
        "dashboard": str(Path(dashboard_service.__file__).resolve()),
        "apps": str(Path(apps_api.__file__).resolve()),
    }
    expected_root = os.getenv("INTEGRAL_BENCHMARK_SOURCE_ROOT")
    if expected_root:
        assert all(
            Path(path).is_relative_to(Path(expected_root).resolve())
            for path in sources.values()
        )
    print("SCOPED_QUERY_SOURCE " + json.dumps(sources), flush=True)

    created = await authenticated_client.post(
        "/api/apps", json={"name": "Scoped query budget", "include_seed_data": False}
    )
    assert created.status_code == 200, created.text
    app = created.json()["app"]
    headers = {"X-Integral-Scope": f"ws:{app['workspace_id']}"}
    created = await authenticated_client.post(
        "/api/tracks",
        json={
            "title": "Measured records",
            "app_id": app["id"],
            "workspace_id": app["workspace_id"],
        },
        headers=headers,
    )
    assert created.status_code == 200, created.text
    track = created.json()["track"]
    for title in ("First measured record", "Second measured record"):
        created = await authenticated_client.post(
            "/api/entries",
            json={"title": title, "track_id": track["id"]},
            headers=headers,
        )
        assert created.status_code == 200, created.text
    widgets = [
        {
            "id": f"metric-{i}",
            "type": "metric_card",
            "title": f"Metric {i}",
            "grid": {"x": i % 3 * 4, "y": i // 3 * 2, "w": 4, "h": 2},
            "data_source": {"kind": "count", "track_id": track["id"]},
        }
        for i in range(10)
    ]
    created = await authenticated_client.post(
        f"/api/apps/{app['id']}/dashboards",
        json={"name": "Ten metrics", "widgets": widgets},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    dashboard_id = created.json()["id"]
    data_url = f"/api/apps/{app['id']}/dashboards/{dashboard_id}/data"
    monkeypatch.setenv("INTEGRAL_PERF_HEADER_ENABLED", "1")
    monkeypatch.setenv("INTEGRAL_PERF_TRACE", "1")
    caplog.set_level(logging.INFO, logger="perf.trace")

    async def measure(*, app_listing=False):
        record_start = len(caplog.records)
        began = time.perf_counter()
        response = await authenticated_client.get(
            "/api/apps" if app_listing else data_url, headers=headers
        )
        elapsed = time.perf_counter() - began
        assert response.status_code == 200, response.text
        if app_listing:
            assert {item["id"] for item in response.json()["apps"]} == {app["id"]}
        else:
            values = response.json()["widget_data"]
            assert len(values) == 10
            assert all(
                row == {"value": 2, "total_matched": 2} for row in values.values()
            )
        requests = [
            record.perf
            for record in caplog.records[record_start:]
            if record.getMessage().startswith("perf.request ")
        ]
        assert len(requests) == 1
        logical_ops = int(response.headers["X-DB-Round-Trip-Count"])
        assert logical_ops > 0
        return {
            "logical_ops": logical_ops,
            "host_load_1m": os.getloadavg()[0],
            "elapsed_ms": elapsed * 1000,
            "summed_db_ms": float(response.headers.get("X-DB-Duration-Ms", "0")),
            **requests[0]["values"],
        }

    apps_before = await measure(app_listing=True)
    before = await measure()
    target = await Track.get(track["id"])
    context = await target.get_context()
    branches = await target.nodes(
        edge=[CATALOGS], node=["Tracks"], direction="in", limit=2
    )
    assert len(branches) == 1
    nodes, edges = [], []
    for i in range(1_000):
        tid = f"n.Track.unrelated-budget-{i}"
        model_id = f"n.OperationalModel.unrelated-budget-{i}"
        nodes.append(
            {
                "id": model_id,
                "entity": OperationalModel._entity_name(),
                "context": {
                    "name": f"Unrelated schema {i}",
                    "workspace_id": app["workspace_id"],
                    "library_package": False,
                },
            }
        )
        edges.append(
            {
                "id": f"e.HAS_OPERATIONAL_MODEL.unrelated-budget-{i}",
                "entity": "HAS_OPERATIONAL_MODEL",
                "source": tid,
                "target": model_id,
                "context": {},
                "bidirectional": False,
            }
        )
        nodes.append(
            {
                "id": tid,
                "entity": Track._entity_name(),
                "context": {
                    "title": f"Unrelated {i}",
                    "workspace_id": app["workspace_id"],
                    "owner_id": track["owner_id"],
                    "visibility": "private",
                },
            }
        )
        edges.append(
            {
                "id": f"e.CATALOGS.unrelated-budget-{i}",
                "entity": "CATALOGS",
                "source": branches[0].id,
                "target": tid,
                "context": {},
                "bidirectional": False,
            }
        )
    # Fixture-only bulk writes reproduce rooted structural catalog edges;
    # measured HTTP requests use normal authentication and live admission.
    database = context.database
    assert await database.bulk_save(context._get_collection_name("n"), nodes) == len(
        nodes
    )
    assert await database.bulk_save(context._get_collection_name("e"), edges) == len(
        edges
    )
    after = await measure()
    apps_after = await measure(app_listing=True)
    samples = int(os.getenv("INTEGRAL_BENCHMARK_SAMPLES", "1"))
    assert 1 <= samples <= 5
    repeated = [after] + [await measure() for _ in range(samples - 1)]
    print(
        "SCOPED_QUERY_BUDGET "
        + json.dumps(
            {
                "before": before,
                "after_1000": after,
                "repeated_after_1000": repeated,
                "apps_before": apps_before,
                "apps_after_1000": apps_after,
            }
        ),
        flush=True,
    )
    if os.getenv("INTEGRAL_BENCHMARK_BASELINE") != "1":
        for initial, grown in ((before, after), (apps_before, apps_after)):
            assert grown["logical_ops"] <= initial["logical_ops"] + 10
            for metric, headroom in (
                ("jvspatial.db.query.driver_statements", 10),
                ("jvspatial.db.query.decoded_chars", 4096),
            ):
                assert metric in initial and metric in grown
                assert grown[metric] <= initial[metric] + headroom
