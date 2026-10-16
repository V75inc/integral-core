"""Reproducible candidate-query probe; not a production latency guarantee.

Run with the disposable native PostgreSQL fixture, ``INTEGRAL_RUN_SLOW_TESTS=1``
and ``-m "postgres and slow"`` (override the default marker exclusion). This
seeds 20,000 events across workspaces, plus unattributed legacy rows, and records
every first/continued query plan in both directions without forcing an index.
A passing probe qualifies pagination correctness and its original narrow plan
gate, not R5: inspect ``history_plan_findings`` for remaining sorts and
residual scans. Tied and varying timestamps are separate fixture shapes.
"""

import json
from datetime import datetime, timedelta, timezone
from time import perf_counter

import pytest
from jvspatial.core.context import GraphContext
from jvspatial.db.postgres import translate_query, translate_sort

from app.services.change_event_logger import ChangeEventLogger
from app.services.pagination import encode_cursor

pytestmark = [
    pytest.mark.contract,
    pytest.mark.postgres,
    pytest.mark.slow,
    pytest.mark.asyncio,
]


@pytest.mark.parametrize("timestamp_shape", ["ties", "ranges"])
async def test_populated_history_keysets_and_query_plan(
    postgres_raw_db, monkeypatch, timestamp_shape, record_property
):
    """Qualify pagination and expose all sampled native plan limitations."""
    context = GraphContext(database=postgres_raw_db)
    logger = ChangeEventLogger()
    monkeypatch.setattr(logger, "_get_log_context", lambda: context)
    await logger.ensure_indexes()
    epoch = datetime(2026, 10, 14, tzinfo=timezone.utc)
    expected = {}
    records = []
    for index in range(20000):
        row_id = f"o.DBLog.load-{index:05}"
        # Non-monotonic timestamps ensure an ID-only implementation cannot pass.
        timestamp = (
            epoch
            + timedelta(
                seconds=(index * 37) % 997 if timestamp_shape == "ranges" else 0
            )
        ).isoformat()
        attribution = (
            {} if index % 101 == 0 else {"workspace_id": f"workspace-{index % 20}"}
        )
        record = {
            "id": row_id,
            "entity": "DBLog",
            "context": {
                "event_code": "entry.update",
                "log_level": "CHANGE_EVENT",
                "logged_at": timestamp,
                "path": "track:probe",
                "method": "",
                "log_data": {
                    "resource_id": "n.Entry.probe",
                    "resource_type": "Entry",
                    **attribution,
                },
            },
        }
        records.append((row_id, "DBLog", json.dumps(record)))
        if attribution.get("workspace_id") in {None, "workspace-7"}:
            expected[row_id] = timestamp
    # Direct COPY is a fixture optimization, never an application write path.
    async with postgres_raw_db._acquire_conn() as connection:
        await connection.copy_records_to_table(
            "object",
            schema_name=postgres_raw_db.schema_name,
            records=records,
            columns=["id", "entity", "data"],
        )
        await connection.execute('ANALYZE "object"')

    queries = []
    find = postgres_raw_db.find

    async def observed(collection, query, **kwargs):
        queries.append((query, kwargs))
        return await find(collection, query, **kwargs)

    monkeypatch.setattr(postgres_raw_db, "find", observed)
    elapsed = {}
    plans = {}
    findings = {}
    for descending in (False, True):
        queries.clear()
        ordered = sorted(
            expected, key=lambda row_id: (expected[row_id], row_id), reverse=descending
        )
        start = perf_counter()
        first = await logger.find_page(
            workspace_id="workspace-7", limit=100, descending=descending, cursor=None
        )
        assert [row.id for row in first] == ordered[:100]
        boundary = first[-1]
        cursor = encode_cursor(boundary.id, boundary.logged_at.isoformat())
        await postgres_raw_db.delete("object", boundary.id)
        second = await logger.find_page(
            workspace_id="workspace-7", limit=100, descending=descending, cursor=cursor
        )
        assert [row.id for row in second] == ordered[100:200]
        first_ids, second_ids = [row.id for row in first], [row.id for row in second]
        assert not set(first_ids) & set(second_ids)
        assert all(
            (
                (expected[row], row) < (expected[boundary.id], boundary.id)
                if descending
                else (expected[row], row) > (expected[boundary.id], boundary.id)
            )
            for row in second_ids
        )
        assert all(
            row.log_data.get("workspace_id") in {None, "workspace-7"}
            for row in first + second
        )
        elapsed[str(descending)] = round((perf_counter() - start) * 1000, 2)
        expected.pop(boundary.id)
        assert len(queries) == 4  # Two disjoint streams per page.

        for query_index, (query, arguments) in enumerate(queries):
            name = f"{'desc' if descending else 'asc'}-{'first' if query_index < 2 else 'continued'}-{'workspace' if query_index % 2 == 0 else 'legacy'}"
            translated = translate_query(query)
            assert translated is not None  # No in-memory scan fallback.
            where, parameters = translated
            order = translate_sort(arguments["sort"])
            assert order is not None
            async with postgres_raw_db._acquire_conn() as connection:
                plan = await connection.fetchval(
                    f'EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT data FROM "object" WHERE {where} ORDER BY {order} LIMIT ${len(parameters) + 1}',
                    *parameters,
                    arguments["limit"],
                )
            parsed = json.loads(plan) if isinstance(plan, str) else plan
            assert 0 <= parsed[0]["Plan"]["Actual Rows"] <= 100
            plan_nodes = list(_plan_nodes(parsed[0]["Plan"]))
            # Retain the original descending/tied continuation plan gate.
            # Other shapes are diagnosed explicitly, never called qualified
            # merely because this correctness/profiling probe succeeds.
            if timestamp_shape == "ties" and descending and query_index >= 2:
                assert parsed[0]["Plan"]["Actual Rows"] == 100
                assert all(
                    node["Node Type"] not in {"Sort", "Seq Scan"} for node in plan_nodes
                ), f"{name}: {json.dumps(parsed)}"
            findings[name] = {
                "sort_or_sequence_scan": any(
                    node["Node Type"] in {"Sort", "Seq Scan"} for node in plan_nodes
                ),
                "rows_removed": sum(
                    node.get("Rows Removed by Filter", 0)
                    + node.get("Rows Removed by Index Recheck", 0)
                    for node in plan_nodes
                ),
                "workspace_index_condition": any(
                    "workspace_id" in node.get("Index Cond", "") for node in plan_nodes
                ),
                "execution_ms": parsed[0]["Execution Time"],
            }
            plans[name] = parsed
    record_property("history_plan_findings", json.dumps(findings, sort_keys=True))
    print(
        json.dumps(
            {
                "seed_rows": len(records),
                "timestamp_shape": timestamp_shape,
                "plan_findings": findings,
                "two_page_ms": elapsed,
                "plans_by_stream": plans,
            },
            sort_keys=True,
        )
    )


def _plan_nodes(node):
    yield node
    for child in node.get("Plans", []):
        yield from _plan_nodes(child)
