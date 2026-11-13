"""Real PostgreSQL contracts for reviewed physical storage changes."""

import os
import uuid

import pytest
from jvspatial.db.indexes import IndexSpec, ManagedSchemaError
from jvspatial.db.postgres import PostgresDB

from app.services.storage_indexes import lifecycle
from app.services.storage_indexes.inspection import inventory, snapshot
from app.services.storage_indexes.proposals import normalize_requests
from app.services.storage_indexes.runtime import inspect

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def storage_pg():
    import asyncpg

    dsn = os.getenv("STORAGE_INDEX_TEST_DSN")
    if not dsn:
        pytest.skip("STORAGE_INDEX_TEST_DSN must identify disposable PostgreSQL")
    conn = await asyncpg.connect(dsn)
    schema = "index_lifecycle_" + uuid.uuid4().hex[:16]
    await conn.execute(f'CREATE SCHEMA "{schema}"')
    try:
        yield conn, schema, dsn
    finally:
        await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await conn.close()


def examples(schema):
    return [
        IndexSpec(
            "item:key",
            "test",
            "object",
            ("(data #>> '{context,key}')",),
            schema=schema,
            predicate="entity = 'Item'",
            unique=True,
            required=True,
            queries=("item:key",),
        ),
        IndexSpec(
            "item:status",
            "test",
            "object",
            ("(data #>> '{context,status}')",),
            schema=schema,
            predicate="entity = 'Item'",
            queries=("item:status",),
        ),
    ]


async def make_plan(conn, schema, specs=None, **kwargs):
    return await lifecycle.plan(
        conn,
        specs or examples(schema),
        schema=schema,
        max_indexes=100,
        max_index_bytes=1_000_000_000,
        max_build_bytes=2_000_000_000,
        include_search=False,
        **kwargs,
    )


async def test_plan_is_read_only_and_hash_bound(storage_pg):
    conn, schema, _ = storage_pg
    artifact = await make_plan(conn, schema)
    assert await inventory(conn, schema) == []
    assert not await conn.fetchval("SELECT to_regclass($1)", schema + ".object")
    with pytest.raises(lifecycle.PlanError, match="Approval"):
        await lifecycle.apply(conn, artifact, approved_hash="wrong")
    artifact["budgets"]["max_indexes"] += 1
    with pytest.raises(lifecycle.PlanError, match="hash"):
        lifecycle.validate_plan(artifact)


async def test_apply_rejects_transaction_before_any_storage_changes(storage_pg):
    conn, schema, _ = storage_pg
    artifact = await make_plan(conn, schema)
    async with conn.transaction():
        with pytest.raises(lifecycle.PlanError, match="autocommit"):
            await lifecycle.apply(conn, artifact, approved_hash=artifact["hash"])
        assert not await conn.fetchval(
            "SELECT to_regclass($1)", schema + ".integral_index_migrations"
        )
    assert await inventory(conn, schema) == []


async def test_fresh_apply_runtime_no_ddl_and_integrity(storage_pg):
    import asyncpg

    conn, schema, dsn = storage_pg
    specs = examples(schema)
    artifact = await make_plan(conn, schema)
    await lifecycle.apply(
        conn, artifact, approved_hash=artifact["hash"], expected_specs=specs
    )
    initial = snapshot(await inventory(conn, schema))
    db = PostgresDB(
        dsn=dsn,
        schema_name=schema,
        index_creation="managed",
        index_catalog_table="integral_index_catalog",
    )
    try:
        await db.verify_index_spec(specs[0])
        for i in range(20):
            await db.create_index("object", f"context.new_{i}")
            await db.save(
                "object", {"id": str(i), "entity": "Item", "context": {"key": str(i)}}
            )
        with pytest.raises(asyncpg.UniqueViolationError):
            await db.save(
                "object", {"id": "duplicate", "entity": "Item", "context": {"key": "0"}}
            )
        assert snapshot(await inventory(conn, schema)) == initial
        assert (await inspect(conn, specs, schema=schema))["ready"]
    finally:
        await db.close()
    await lifecycle.apply(conn, artifact, approved_hash=artifact["hash"])
    assert snapshot(await inventory(conn, schema)) == initial


async def test_rejects_drift_and_changed_source(storage_pg):
    conn, schema, _ = storage_pg
    artifact = await make_plan(conn, schema)
    with pytest.raises(lifecycle.PlanError, match="Source"):
        await lifecycle.apply(
            conn, artifact, approved_hash=artifact["hash"], expected_specs=[]
        )
    await conn.execute(f'CREATE TABLE "{schema}".unexpected(id int PRIMARY KEY)')
    with pytest.raises(lifecycle.PlanError, match="Unexpected"):
        await lifecycle.apply(conn, artifact, approved_hash=artifact["hash"])


async def test_resume_after_build_before_receipt(storage_pg, monkeypatch):
    conn, schema, _ = storage_pg
    artifact = await make_plan(conn, schema)
    receipt = lifecycle._receipt
    calls = 0

    async def crash(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic crash")
        return await receipt(*args)

    monkeypatch.setattr(lifecycle, "_receipt", crash)
    with pytest.raises(RuntimeError, match="synthetic"):
        await lifecycle.apply(conn, artifact, approved_hash=artifact["hash"])
    count = len(await inventory(conn, schema))
    result = await lifecycle.apply(conn, artifact, approved_hash=artifact["hash"])
    assert result["state"] == "complete"
    assert len(await inventory(conn, schema)) == count + 1


async def test_resume_rejects_changed_bootstrap_primary_key(storage_pg, monkeypatch):
    conn, schema, _ = storage_pg
    artifact = await make_plan(conn, schema)
    receipt = lifecycle._receipt

    async def crash(*args):
        raise RuntimeError("synthetic crash")

    monkeypatch.setattr(lifecycle, "_receipt", crash)
    with pytest.raises(RuntimeError, match="synthetic"):
        await lifecycle.apply(conn, artifact, approved_hash=artifact["hash"])
    monkeypatch.setattr(lifecycle, "_receipt", receipt)
    await conn.execute(f'ALTER TABLE "{schema}".node DROP CONSTRAINT node_pkey')
    await conn.execute(f'ALTER TABLE "{schema}".node ADD PRIMARY KEY(entity)')
    with pytest.raises(lifecycle.PlanError, match="Unexpected"):
        await lifecycle.apply(conn, artifact, approved_hash=artifact["hash"])


async def test_competing_runner_and_unsafe_retirement(storage_pg):
    import asyncpg

    conn, schema, dsn = storage_pg
    artifact = await make_plan(conn, schema)
    other = await asyncpg.connect(dsn)
    try:
        await other.execute(
            "SELECT pg_advisory_lock(hashtext(current_database()),hashtext('integral:indexes'))"
        )
        with pytest.raises(lifecycle.PlanError, match="Another"):
            await lifecycle.apply(conn, artifact, approved_hash=artifact["hash"])
    finally:
        await other.close()
    await lifecycle.apply(conn, artifact, approved_hash=artifact["hash"])
    with pytest.raises(lifecycle.PlanError, match="primary"):
        await make_plan(conn, schema, removals=["object_pkey"])
    with pytest.raises(lifecycle.PlanError, match="coverage"):
        await make_plan(conn, schema, removals=[examples(schema)[1].name])


async def test_managed_missing_table_or_constraint_fails_closed(storage_pg):
    conn, schema, dsn = storage_pg
    db = PostgresDB(
        dsn=dsn,
        schema_name=schema,
        index_creation="managed",
        index_catalog_table="integral_index_catalog",
    )
    try:
        with pytest.raises(ManagedSchemaError):
            await db.get("node", "missing")
        with pytest.raises(ManagedSchemaError):
            await db.verify_index_spec(examples(schema)[0])
        assert await inventory(conn, schema) == []
    finally:
        await db.close()


async def test_runtime_role_cannot_create_indexes(storage_pg):
    from urllib.parse import urlsplit

    import asyncpg

    conn, schema, dsn = storage_pg
    role = "index_runtime_" + uuid.uuid4().hex[:12]
    await conn.execute(f'CREATE ROLE "{role}" LOGIN')
    artifact = await make_plan(conn, schema, runtime_role=role)
    try:
        await lifecycle.apply(conn, artifact, approved_hash=artifact["hash"])
        parsed = urlsplit(dsn)
        runtime = await asyncpg.connect(
            host=parsed.hostname, port=parsed.port, database=parsed.path[1:], user=role
        )
        try:
            assert (
                await inspect(
                    runtime, examples(schema), schema=schema, enforce_runtime_role=True
                )
            )["ready"]
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await runtime.execute(
                    f'CREATE INDEX forbidden ON "{schema}".object(entity)'
                )
            await runtime.execute(
                f"INSERT INTO \"{schema}\".object(id,entity,data) VALUES ('safe','Item','{{}}')"
            )
        finally:
            await runtime.close()
    finally:
        await conn.execute(f'DROP OWNED BY "{role}"')
        await conn.execute(f'DROP ROLE "{role}"')


async def test_optional_missing_is_degraded_required_missing_is_unready(storage_pg):
    conn, schema, _ = storage_pg
    specs = examples(schema)
    artifact = await make_plan(conn, schema)
    await lifecycle.apply(conn, artifact, approved_hash=artifact["hash"])
    await conn.execute(f'DROP INDEX "{schema}"."{specs[1].name}"')
    state = await inspect(conn, specs, schema=schema)
    assert state["ready"] and state["optional_missing"] == [specs[1].logical_id]
    await conn.execute(f'DROP INDEX "{schema}"."{specs[0].name}"')
    assert not (await inspect(conn, specs, schema=schema))["ready"]


async def test_app_requests_cannot_provision_or_supply_sql():
    queries = [{"key": "orders"}]
    tracks = [
        {
            "key": "orders",
            "entry_types": [
                {"key": "order", "fields": [{"key": "amount", "type": "number"}]}
            ],
        }
    ]
    request = {
        "key": "by_amount",
        "query_key": "orders",
        "track_key": "orders",
        "entry_type_key": "order",
        "fields": [{"field": "amount", "direction": -1}],
    }
    assert (
        normalize_requests([request], queries=queries, tracks=tracks)[0]["state"]
        == "pending_platform_review"
    )
    for extra in ({"sql": "CREATE INDEX"}, {"predicate": "true"}, {"name": "chosen"}):
        with pytest.raises(ValueError):
            normalize_requests([{**request, **extra}], queries=queries, tracks=tracks)


async def test_expression_matching_keeps_function_and_type_semantics():
    from app.services.storage_indexes.inspection import normalized

    assert normalized("NULLIF((data #> '{x}'),'null'::jsonb)") == normalized(
        "NULLIF(data #> '{x}'::text[],'null'::jsonb)"
    )
    assert normalized("lower(data #>> '{x}')") != normalized("data #>> '{x}'")
    assert normalized("(data #>> '{x}')::numeric") != normalized("data #>> '{x}'")


async def test_invalid_unique_build_requires_explicit_repair(storage_pg):
    import asyncpg

    conn, schema, dsn = storage_pg
    db = PostgresDB(dsn=dsn, schema_name=schema)
    try:
        for key in ("a", "b"):
            await db.save(
                "object", {"id": key, "entity": "Item", "context": {"key": "duplicate"}}
            )
    finally:
        await db.close()
    spec = examples(schema)[0]
    with pytest.raises(asyncpg.UniqueViolationError):
        await conn.execute(spec.create_sql())
    with pytest.raises(lifecycle.PlanError, match="repair"):
        await make_plan(conn, schema)
    await conn.execute(f"DELETE FROM \"{schema}\".object WHERE id='b'")
    artifact = await make_plan(conn, schema, repairs=[spec.name])
    await lifecycle.apply(conn, artifact, approved_hash=artifact["hash"])
    assert (await inspect(conn, examples(schema), schema=schema))["ready"]


async def test_optional_index_budget_never_removes_integrity(storage_pg):
    conn, schema, _ = storage_pg
    with pytest.raises(lifecycle.PlanError, match="budget"):
        await lifecycle.plan(
            conn,
            examples(schema),
            schema=schema,
            max_indexes=1,
            max_index_bytes=1_000_000,
            max_build_bytes=2_000_000,
            include_search=False,
        )
    assert await inventory(conn, schema) == []


async def test_growth_after_review_blocks_new_performance_index(storage_pg):
    conn, schema, _ = storage_pg
    required, optional = examples(schema)
    initial = await make_plan(conn, schema, specs=[required])
    await lifecycle.apply(conn, initial, approved_hash=initial["hash"])
    rows = await inventory(conn, schema)
    budget = sum(row["bytes"] for row in rows) + 32768
    artifact = await lifecycle.plan(
        conn,
        [required, optional],
        schema=schema,
        max_indexes=100,
        max_index_bytes=budget,
        max_build_bytes=2_000_000_000,
        include_search=False,
    )
    await conn.execute(
        f'INSERT INTO "{schema}".object(id,entity,data) '
        "SELECT i::text,'Item',jsonb_build_object('context',jsonb_build_object('key',i::text)) "
        "FROM generate_series(1,1000) i"
    )
    with pytest.raises(lifecycle.PlanError, match="Steady-state budget"):
        await lifecycle.apply(conn, artifact, approved_hash=artifact["hash"])
    rows = {row["name"]: row for row in await inventory(conn, schema)}
    assert required.name in rows and rows[required.name]["valid"]
    assert optional.name not in rows


async def test_resume_drop_after_concurrent_cancellation(storage_pg):
    import asyncio

    import asyncpg
    from jvspatial.db.indexes import definition_digest

    conn, schema, dsn = storage_pg
    specs = examples(schema)
    initial = await make_plan(conn, schema)
    await lifecycle.apply(conn, initial, approved_hash=initial["hash"])
    old_name = "legacy_status"
    await conn.execute(specs[1].create_sql().replace(specs[1].name, old_name))
    old = next(row for row in await inventory(conn, schema) if row["name"] == old_name)
    artifact = await make_plan(
        conn,
        schema,
        removals=[old_name],
        retirement_evidence={
            old_name: {
                "definition_hash": definition_digest(old["definition"]),
                "replacement_fingerprints": [specs[1].fingerprint],
                "queries": list(specs[1].queries),
                "qualification_sha256": "a" * 64,
            }
        },
    )
    reader = await asyncpg.connect(dsn)
    observer = await asyncpg.connect(dsn)
    task = None
    try:
        async with reader.transaction():
            await reader.fetch(f'SELECT * FROM "{schema}".object LIMIT 1')
            task = asyncio.create_task(
                lifecycle.apply(conn, artifact, approved_hash=artifact["hash"])
            )
            async with asyncio.timeout(8):
                while True:
                    remaining = next(
                        (
                            row
                            for row in await inventory(observer, schema)
                            if row["name"] == old_name
                        ),
                        None,
                    )
                    assert remaining is not None
                    if not remaining["valid"]:
                        break
                    await asyncio.sleep(0.02)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        result = await lifecycle.apply(conn, artifact, approved_hash=artifact["hash"])
        assert result["state"] == "complete"
        assert old_name not in {row["name"] for row in await inventory(conn, schema)}
        assert (await inspect(conn, specs, schema=schema))["ready"]
        await conn.execute(old["definition"])
        with pytest.raises(lifecycle.PlanError, match="reappeared"):
            await lifecycle.apply(conn, artifact, approved_hash=artifact["hash"])
    finally:
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await reader.close()
        await observer.close()
