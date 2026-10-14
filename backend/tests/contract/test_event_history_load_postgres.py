"""Reproducible candidate-query probe; not a production latency guarantee.

Run with the disposable native PostgreSQL fixture and ``-m postgres``. This
seeds 20,000 events across workspaces, plus unattributed legacy rows, and records
the adapter's actual translated query plan without forcing an index scan.
"""

import json
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


async def test_populated_history_keysets_and_query_plan(postgres_raw_db, monkeypatch):
    context = GraphContext(database=postgres_raw_db)
    logger = ChangeEventLogger()
    monkeypatch.setattr(logger, "_get_log_context", lambda: context)
    await logger.ensure_indexes()
    timestamp = "2026-10-14T00:00:00+00:00"
    records = []
    for index in range(20000):
        row_id = f"o.DBLog.load-{index:05}"
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
    for descending in (False, True):
        start = perf_counter()
        first = await logger.find_page(
            workspace_id="workspace-7", limit=100, descending=descending, cursor=None
        )
        assert len(first) == 100
        boundary = first[-1]
        cursor = encode_cursor(boundary.id, boundary.logged_at.isoformat())
        await postgres_raw_db.delete("object", boundary.id)
        second = await logger.find_page(
            workspace_id="workspace-7", limit=100, descending=descending, cursor=cursor
        )
        assert len(second) == 100
        first_ids, second_ids = [row.id for row in first], [row.id for row in second]
        assert not set(first_ids) & set(second_ids)
        assert all(
            (row < boundary.id if descending else row > boundary.id)
            for row in second_ids
        )
        assert all(
            row.log_data.get("workspace_id") in {None, "workspace-7"}
            for row in first + second
        )
        elapsed[str(descending)] = round((perf_counter() - start) * 1000, 2)

    plans = {}
    for name, (query, arguments) in zip(("workspace", "legacy"), queries[-2:]):
        translated = translate_query(query)
        assert translated is not None  # Never rely on an in-memory scan fallback.
        where, parameters = translated
        order = translate_sort(arguments["sort"])
        assert order is not None
        async with postgres_raw_db._acquire_conn() as connection:
            plan = await connection.fetchval(
                f'EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT data FROM "object" WHERE {where} ORDER BY {order} LIMIT 100',
                *parameters,
            )
        parsed = json.loads(plan) if isinstance(plan, str) else plan
        assert parsed[0]["Plan"]["Actual Rows"] == 100

        def nodes(node):
            yield node
            for child in node.get("Plans", []):
                yield from nodes(child)

        assert all(
            node["Node Type"] not in {"Sort", "Seq Scan"}
            for node in nodes(parsed[0]["Plan"])
        ), f"{name}: {json.dumps(parsed)}"
        plans[name] = parsed
    print(
        json.dumps(
            {
                "seed_rows": len(records),
                "two_page_ms": elapsed,
                "plans_by_stream": plans,
            },
            sort_keys=True,
        )
    )
