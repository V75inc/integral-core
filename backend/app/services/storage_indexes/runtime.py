"""Verify-only runtime readiness; all expensive inspection is outside saves."""

import asyncio
import logging
import os

log = logging.getLogger(__name__)
_state = {"ready": False, "optional_missing": 0, "reason": "not_checked"}


def managed():
    """Determine whether PostgreSQL serving uses the strict lifecycle boundary."""
    return (
        os.getenv("JVSPATIAL_DB_TYPE", "").strip().lower() in {"postgres", "postgresql"}
        and os.getenv("JVSPATIAL_POSTGRES_INDEX_CREATION", "managed").strip().lower()
        == "managed"
    )


def postgres_kwargs():
    """Require supported and non-conflicting serving storage configuration."""
    try:
        from jvspatial.db.indexes import ManagedSchemaError
    except ImportError:
        raise RuntimeError(
            "Selected foundation lacks managed storage capability"
        ) from None
    mode = (
        os.environ.get("JVSPATIAL_POSTGRES_INDEX_CREATION", "managed").strip().lower()
    )
    os.environ["JVSPATIAL_POSTGRES_INDEX_CREATION"] = mode
    if mode not in {"managed", "constraints"}:
        raise ManagedSchemaError(
            "Core serving processes require managed storage; constraints is transitional"
        )
    if mode == "managed" and any(
        value
        for key, value in os.environ.items()
        if key.startswith(("INTEGRAL_MIGRATION_", "INDEX_MIGRATION_"))
    ):
        raise ManagedSchemaError(
            "Maintenance credentials must not be present in serving processes"
        )
    if mode == "managed" and os.getenv(
        "JVSPATIAL_AUTO_CREATE_INDEXES", "true"
    ).strip().lower() not in {"1", "true", "yes", "on"}:
        raise ManagedSchemaError(
            "Managed mode requires integrity declarations to remain enabled"
        )
    return {"index_creation": mode}


def state():
    """Return cached readiness without catalog queries on request paths."""
    return dict(_state)


async def inspect(conn, specs, *, schema="public", enforce_runtime_role=False):
    """Report correctness, degraded capabilities, migration progress and budgets."""
    from jvspatial.db.indexes import definition_digest

    from .catalog import revision
    from .inspection import inventory, runtime_privileges
    from .lifecycle import registered

    receipts = await registered(conn, schema)
    # Approved App/profile requirements survive restarts independently of the
    # maintenance-only source files. Runtime cannot modify these receipts.
    from jvspatial import IndexSpec

    specs = list(specs)
    source_fingerprints = {s.fingerprint for s in specs}
    for fingerprint, receipt in receipts.items():
        import json

        payload = receipt["spec"]
        payload = json.loads(payload) if isinstance(payload, str) else payload
        if fingerprint not in source_fingerprints and (
            payload["owner"].startswith("app:")
            or payload["logical_id"].startswith("profile-ann:")
        ):
            approved = IndexSpec.from_dict(payload)
            if approved.fingerprint != fingerprint or approved.schema != schema:
                raise ValueError("Approved catalog receipt is inconsistent")
            specs.append(approved)
    active_fingerprints = {s.fingerprint for s in specs}
    active_names = {
        r["index_name"] for fp, r in receipts.items() if fp in active_fingerprints
    }
    rows = await inventory(conn, schema)
    by_name = {r["name"]: r for r in rows}
    required, optional = [], []
    for spec in specs:
        receipt = receipts.get(spec.fingerprint)
        row = by_name.get(receipt["index_name"]) if receipt else None
        good = (
            row
            and row["valid"]
            and row["ready"]
            and definition_digest(row["definition"]) == receipt["definition_hash"]
        )
        if not good:
            (required if spec.required else optional).append(spec.logical_id)
    required_tables = {
        s.table for s in specs if s.required and s.table in {"object", "node", "edge"}
    }
    columns = await conn.fetch(
        "SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod) AS type FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid WHERE n.nspname=$1 AND c.relname=ANY($2::text[]) AND a.attnum>0 AND NOT a.attisdropped",
        schema,
        list(required_tables),
    )
    shapes = {}
    for column in columns:
        shapes.setdefault(column["relname"], {})[column["attname"]] = column["type"]
    expected = {
        "id": "text",
        "entity": "text",
        "tenant_id": "text",
        "data": "jsonb",
        "_v": "integer",
        "created_at": "timestamp with time zone",
        "updated_at": "timestamp with time zone",
    }
    relation_mismatch = [
        table
        for table in required_tables
        if any(shapes.get(table, {}).get(key) != kind for key, kind in expected.items())
    ]
    missing_data_privileges = []
    if enforce_runtime_role:
        for table in required_tables - set(relation_mismatch):
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                if not await conn.fetchval(
                    "SELECT has_table_privilege(current_user,$1,$2)",
                    schema + "." + table,
                    privilege,
                ):
                    missing_data_privileges.append(table + ":" + privilege)
    budget_violations = []
    migration = None
    migrations = []
    if await conn.fetchval(
        "SELECT to_regclass($1)", schema + ".integral_index_migrations"
    ):
        migrations = [
            dict(row)
            for row in await conn.fetch(
                f'SELECT plan_hash,state,jsonb_array_length(completed_ops) AS completed_operations,active_op,error_type,updated_at::text FROM "{schema}".integral_index_migrations ORDER BY updated_at DESC LIMIT 10'
            )
        ]
        migration = await conn.fetchval(
            f'SELECT artifact FROM "{schema}".integral_index_migrations WHERE state=$1 ORDER BY updated_at DESC LIMIT 1',
            "complete",
        )
    if migration:
        import json

        artifact = json.loads(migration) if isinstance(migration, str) else migration
        budget = artifact["budgets"]
        if len(rows) > budget["max_indexes"]:
            budget_violations.append("index_count")
        if sum(r["bytes"] for r in rows) > budget["max_index_bytes"]:
            budget_violations.append("index_bytes")
    privileged = enforce_runtime_role and await runtime_privileges(conn, schema)
    return {
        "ready": not required
        and not privileged
        and not relation_mismatch
        and not missing_data_privileges
        and bool(receipts),
        "required_relation_mismatch": relation_mismatch,
        "missing_data_privileges": missing_data_privileges,
        "catalog_revision": revision(specs),
        "required_missing": required,
        "optional_missing": optional,
        "runtime_role_has_ddl": bool(privileged),
        "unmanaged": [
            r["name"]
            for r in rows
            if r["name"] not in {v["index_name"] for v in receipts.values()}
        ],
        "obsolete_managed": sorted(
            {
                r["index_name"]
                for fp, r in receipts.items()
                if fp not in active_fingerprints and r["index_name"] not in active_names
            }
        ),
        "indexes": len(rows),
        "index_bytes": sum(r["bytes"] for r in rows),
        "budget_violations": budget_violations,
        "invalid": [r["name"] for r in rows if not r["valid"] or not r["ready"]],
        "migrations": migrations,
        "build_progress": [
            dict(row)
            for row in await conn.fetch(
                "SELECT phase,blocks_total,blocks_done,tuples_total,tuples_done FROM pg_stat_progress_create_index WHERE datname=current_database()"
            )
        ],
    }


async def verify(*, database=None, specs=None):
    """Check provisioned integrity and serving privileges before accepting traffic."""
    from jvspatial.db.indexes import ManagedSchemaError

    from .catalog import build_catalog

    if not managed():
        return {"ready": True, "mode": "transitional"}
    from jvspatial.db import get_prime_database

    db = database or get_prime_database()
    while not hasattr(db, "index_creation") and hasattr(db, "inner"):
        db = db.inner
    if getattr(db, "index_creation", None) != "managed":
        raise ManagedSchemaError("Selected foundation does not support managed storage")
    import asyncpg

    conn = await asyncpg.connect(db.dsn, command_timeout=15)
    try:
        result = await inspect(
            conn,
            specs or build_catalog(schema=db.schema_name),
            schema=db.schema_name,
            enforce_runtime_role=True,
        )
    finally:
        await conn.close()
    log_dsn = os.getenv("JVSPATIAL_LOG_POSTGRES_DSN", "")
    if log_dsn and log_dsn != db.dsn:
        log_conn = await asyncpg.connect(log_dsn, command_timeout=15)
        try:
            log_specs = [
                s
                for s in build_catalog(schema=db.schema_name)
                if s.table == "object"
                and (
                    s.logical_id.startswith("DBLog:")
                    or s.owner == "core.events"
                    or s.logical_id == "object:primary"
                )
            ]
            log_result = await inspect(
                log_conn, log_specs, schema=db.schema_name, enforce_runtime_role=True
            )
            result["ready"] = result["ready"] and log_result["ready"]
        finally:
            await log_conn.close()
    if result["optional_missing"] and _state.get("optional_missing") != len(
        result["optional_missing"]
    ):
        log.warning(
            "Managed storage has missing optional indexes; inspect degraded capabilities with indexes status"
        )
    _state.update(
        ready=result["ready"],
        optional_missing=len(result["optional_missing"]),
        reason="ready" if result["ready"] else "storage_catalog_unready",
    )
    if result["budget_violations"]:
        log.warning(
            "Managed storage budget exceeded; additional performance provisioning requires review"
        )
    if not result["ready"]:
        raise ManagedSchemaError(
            "Required storage catalog or runtime role is not ready; run indexes status"
        )
    db._managed_verified_indexes.update(
        s.fingerprint
        for s in (specs or build_catalog(schema=db.schema_name))
        if s.required
    )
    return result


async def monitor():
    """Refresh cached readiness periodically outside business save requests."""
    from .catalog import build_catalog

    specs = build_catalog()
    while True:
        await asyncio.sleep(60)
        try:
            await verify(specs=specs)
        except asyncio.CancelledError:
            raise
        except Exception:
            _state.update(ready=False, reason="storage_catalog_unready")
            log.error("Managed storage readiness failed; operator inspection required")


async def verify_relation(conn, table, columns, *, schema="public"):
    """Reject a missing or incompatible search relation without repairing it."""
    from jvspatial.db.indexes import ManagedSchemaError

    rows = await conn.fetch(
        "SELECT a.attname,format_type(a.atttypid,a.atttypmod) AS type FROM pg_attribute a WHERE a.attrelid=to_regclass($1) AND a.attnum>0 AND NOT a.attisdropped",
        schema + "." + table,
    )
    actual = {r["attname"]: r["type"] for r in rows}
    if any(
        name not in actual or (kind and actual[name] != kind)
        for name, kind in columns.items()
    ):
        raise ManagedSchemaError("Required storage relation is missing or incompatible")


async def verify_search_indexes(conn, specs):
    """Fail a search capability honestly; never silently scan without its profile."""
    from jvspatial import ManagedSchemaError

    status = await inspect(conn, specs)
    if status["required_missing"] or status["optional_missing"]:
        raise ManagedSchemaError("Search indexes require reviewed provisioning")
