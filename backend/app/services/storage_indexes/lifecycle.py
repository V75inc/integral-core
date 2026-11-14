"""Read-only planning, bounded operator DDL, and resumable migration receipts."""

from __future__ import annotations

import json

from jvspatial.db.indexes import IndexSpec, definition_digest
from jvspatial.db.indexes import identifier as q

from . import catalog
from .inspection import definition_matches, digest, inventory, snapshot, target


class PlanError(RuntimeError):
    pass


def source_versions():
    """Bind a reviewed artifact to selected platform and foundation releases."""
    from importlib.metadata import PackageNotFoundError, version

    import jvspatial

    try:
        core_version = version("integral-core")
    except PackageNotFoundError:
        core_version = "source"
    import hashlib
    from pathlib import Path

    from jvspatial.db import indexes as foundation_indexes
    from jvspatial.db import postgres as foundation_postgres

    files = [
        *sorted(Path(__file__).parent.glob("*.py")),
        Path(__file__).with_name("approved.json"),
        Path(foundation_indexes.__file__),
        Path(foundation_postgres.__file__),
    ]
    compiler_hash = hashlib.sha256()
    for path in files:
        compiler_hash.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return {
        "core": core_version,
        "foundation": jvspatial.__version__,
        "catalog_format": catalog.VERSION,
        "implementation_sha256": compiler_hash.hexdigest(),
    }


async def registered(conn, schema):
    """Read durable approved-definition receipts without graph initialization."""
    if not await conn.fetchval(
        "SELECT to_regclass($1)", schema + ".integral_index_catalog"
    ):
        return {}
    return {
        r["fingerprint"]: dict(r)
        for r in await conn.fetch(f"SELECT * FROM {q(schema)}.integral_index_catalog")
    }


async def plan(conn, specs, **kwargs):
    """Planning remains read-only even during expression inspection."""
    async with conn.transaction(readonly=True):
        return await _plan(conn, specs, **kwargs)


async def _plan(
    conn,
    specs,
    *,
    schema="public",
    max_indexes,
    max_index_bytes,
    max_build_bytes,
    include_search=True,
    removals=(),
    runtime_role=None,
    repairs=(),
    retirement_evidence=None,
):
    if any(
        not isinstance(n, int) or n <= 0
        for n in (max_indexes, max_index_bytes, max_build_bytes)
    ):
        raise PlanError(
            "Positive count, steady-state and build-space budgets are required"
        )
    if runtime_role:
        q(runtime_role)
    rows = await inventory(conn, schema)
    by_name = {r["name"]: r for r in rows}
    receipts = await registered(conn, schema)
    replacements = []
    for spec in specs:
        for fingerprint, receipt in receipts.items():
            previous = receipt["spec"]
            previous = json.loads(previous) if isinstance(previous, str) else previous
            if (
                fingerprint != spec.fingerprint
                and previous["logical_id"] == spec.logical_id
                and previous["owner"] == spec.owner
            ):
                replacements.append(
                    {
                        "logical_id": spec.logical_id,
                        "previous_fingerprint": fingerprint,
                        "previous_index": receipt["index_name"],
                        "replacement_fingerprint": spec.fingerprint,
                        "retirement_requires_separate_qualification": True,
                    }
                )
    operations, estimates = [], 0
    tables = await conn.fetch(
        "SELECT relname, greatest(reltuples,0)::bigint AS rows FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=$1 AND c.relkind IN ('r','p')",
        schema,
    )
    counts = {r["relname"]: r["rows"] for r in tables}
    schema_sql = catalog.collection_sql(schema)
    if include_search:
        from .search import search_schema_sql

        schema_sql += search_schema_sql(schema)
    for sql in schema_sql:
        operations.append({"kind": "schema", "sql": sql})
    for spec in specs:
        row = by_name.get(spec.name)
        if row is None:
            for candidate in rows:
                if (
                    candidate["table_name"] != spec.table
                    or candidate["unique"] != spec.unique
                    or candidate["method"] != spec.method
                    or len(candidate["keys"]) != len(spec.keys)
                ):
                    continue
                from .inspection import key_semantics

                if [key_semantics(k)[0] for k in candidate["keys"]] != [
                    key_semantics(k)[0] for k in spec.keys
                ]:
                    continue
                if await definition_matches(conn, spec, candidate):
                    row = candidate
                    break
        if (
            row is None
            and spec.logical_id.endswith(":primary")
            and spec.table not in counts
        ):
            operations.append(
                {
                    "kind": "register",
                    "spec": spec.to_dict(),
                    "index_name": spec.table + "_pkey",
                    "expected_definition": None,
                }
            )
            continue
        if row and await definition_matches(conn, spec, row):
            receipt = receipts.get(spec.fingerprint)
            if receipt:
                previous = receipt["spec"]
                previous = (
                    json.loads(previous) if isinstance(previous, str) else previous
                )
                if (
                    receipt["index_name"] == row["name"]
                    and receipt["definition_hash"]
                    == definition_digest(row["definition"])
                    and digest(previous) == digest(spec.to_dict())
                ):
                    continue
            operations.append(
                {
                    "kind": "register",
                    "spec": spec.to_dict(),
                    "expected_definition": row["definition"],
                    "index_name": row["name"],
                }
            )
        elif (
            row
            and spec.name in repairs
            and await definition_matches(
                conn, spec, {**row, "valid": True, "ready": True}
            )
        ):
            operations.append(
                {
                    "kind": "repair",
                    "spec": spec.to_dict(),
                    "sql": f"REINDEX INDEX CONCURRENTLY {q(schema)}.{q(spec.name)}",
                    "estimated_bytes": max(row["bytes"], 16384),
                    "expected_definition": row["definition"],
                }
            )
            estimates += max(row["bytes"], 16384)
        elif row:
            raise PlanError(
                "A desired index name has a conflicting or invalid definition; explicit repair is required"
            )
        else:
            estimate = max(16384, counts.get(spec.table, 0) * 96 * len(spec.keys))
            estimates += estimate
            operations.append(
                {
                    "kind": "create",
                    "spec": spec.to_dict(),
                    "estimated_bytes": estimate,
                    "sql": spec.create_sql(),
                    "rollback_sql": f"DROP INDEX CONCURRENTLY {q(schema)}.{q(spec.name)}",
                }
            )
    for name in removals:
        row = by_name.get(name)
        if not row or row["primary"] or row["constraint_backed"] or row["unique"]:
            raise PlanError(
                "Cannot remove a missing, primary, unique or constraint-backed index"
            )
        proof = (retirement_evidence or {}).get(name, {})
        replacement = proof.get("replacement_fingerprints", [])
        candidates = {s.fingerprint: s for s in specs if s.table == row["table_name"]}
        queries = {
            key
            for fp in replacement
            if fp in candidates
            for key in candidates[fp].queries
        }
        import re

        if (
            proof.get("definition_hash") != definition_digest(row["definition"])
            or not replacement
            or any(fp not in candidates for fp in replacement)
            or not proof.get("queries")
            or not set(proof["queries"]).issubset(queries)
            or not re.fullmatch(r"[a-f0-9]{64}", proof.get("qualification_sha256", ""))
        ):
            raise PlanError(
                "Removal requires definition-bound, qualified replacement query coverage"
            )
        operations.append(
            {
                "kind": "drop",
                "name": name,
                "expected_definition": row["definition"],
                "replacement_fingerprints": replacement,
                "qualification": proof,
                "sql": f"DROP INDEX CONCURRENTLY {q(schema)}.{q(name)}",
                "rollback_sql": row["definition"].replace(
                    "CREATE INDEX ", "CREATE INDEX CONCURRENTLY ", 1
                ),
            }
        )
    existing_bytes = sum(r["bytes"] for r in rows)
    projected_count = (
        len(rows)
        + sum(op["kind"] == "create" for op in operations)
        - len(removals)
        + sum(
            name not in counts
            for name in (
                [
                    "object",
                    "node",
                    "edge",
                    "integral_index_catalog",
                    "integral_index_migrations",
                ]
                + (
                    [
                        "entry_embedding",
                        "entry_embedding_passage",
                        "entry_embedding_lexical",
                        "entry_embedding_manifest",
                    ]
                    if include_search
                    else []
                )
            )
        )
    )
    removed_bytes = sum(by_name[name]["bytes"] for name in removals)
    if (
        projected_count > max_indexes
        or existing_bytes + estimates - removed_bytes > max_index_bytes
        or existing_bytes + estimates > max_build_bytes
    ):
        raise PlanError("Proposed catalog exceeds the reviewed count or storage budget")
    payload = {
        "format": 1,
        "source_versions": source_versions(),
        "control_schema": control_schema_sql(schema),
        "catalog_revision": catalog.revision(specs),
        "target": await target(conn, schema),
        "specs": [s.to_dict() for s in specs],
        "include_search": include_search,
        "starting_inventory": snapshot(rows),
        "operations": operations,
        "replacements": replacements,
        "runtime_role": runtime_role,
        "budgets": {
            "max_indexes": max_indexes,
            "max_index_bytes": max_index_bytes,
            "max_build_bytes": max_build_bytes,
        },
        "estimates": {
            "new_index_bytes": estimates,
            "method": "conservative table rows x 96 bytes x keys; verify on populated clone",
        },
    }
    return {**payload, "hash": digest(payload)}


def validate_plan(artifact):
    """Reject altered artifacts and unreviewed schema or operation definitions."""
    payload = {k: v for k, v in artifact.items() if k != "hash"}
    if artifact.get("format") != 1 or artifact.get("hash") != digest(payload):
        raise PlanError("Plan content hash is invalid")
    if artifact.get("source_versions") != source_versions():
        raise PlanError("Source artifact versions changed")
    specs = [IndexSpec.from_dict(s) for s in artifact["specs"]]
    if catalog.revision(specs) != artifact["catalog_revision"]:
        raise PlanError("Plan catalog revision is invalid")
    schema = artifact["target"]["schema"]
    if artifact.get("control_schema", control_schema_sql(schema)) != control_schema_sql(
        schema
    ):
        raise PlanError("Control schema differs from compiled catalog")
    allowed_schema = catalog.collection_sql(schema)
    if artifact["include_search"]:
        from .search import search_schema_sql

        allowed_schema += search_schema_sql(schema)
    for op in artifact["operations"]:
        if op["kind"] == "schema" and op["sql"] not in allowed_schema:
            raise PlanError("Unknown schema operation")
        if op["kind"] in {"create", "register", "repair"}:
            spec = IndexSpec.from_dict(op["spec"])
            if (
                spec not in specs
                or (op["kind"] == "create" and op["sql"] != spec.create_sql())
                or (
                    op["kind"] == "repair"
                    and op["sql"]
                    != f"REINDEX INDEX CONCURRENTLY {q(schema)}.{q(spec.name)}"
                )
            ):
                raise PlanError("Index operation differs from catalog")
        elif op["kind"] == "drop":
            if op["sql"] != f"DROP INDEX CONCURRENTLY {q(schema)}.{q(op['name'])}":
                raise PlanError("Invalid removal operation")
        elif op["kind"] != "schema":
            raise PlanError("Unknown plan operation")
    return specs


def control_schema_sql(schema):
    """Describe durable migration bookkeeping independently of graph state."""
    return [
        f"CREATE TABLE IF NOT EXISTS {q(schema)}.integral_index_catalog (fingerprint text PRIMARY KEY, index_name text NOT NULL, spec jsonb NOT NULL, definition_hash text NOT NULL, plan_hash text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())",
        f"CREATE TABLE IF NOT EXISTS {q(schema)}.integral_index_migrations (plan_hash text PRIMARY KEY, artifact jsonb NOT NULL, state text NOT NULL, completed_ops jsonb NOT NULL DEFAULT '[]', active_op integer, error_type text, updated_at timestamptz NOT NULL DEFAULT now())",
    ]


async def _control_schema(conn, schema):
    for sql in control_schema_sql(schema):
        await conn.execute(sql)


async def _receipt(conn, schema, spec, row, plan_hash):
    if not await definition_matches(conn, spec, row):
        raise PlanError("Built index does not match its reviewed definition")
    await conn.execute(
        f"INSERT INTO {q(schema)}.integral_index_catalog (fingerprint,index_name,spec,definition_hash,plan_hash) VALUES ($1,$2,$3::jsonb,$4,$5) ON CONFLICT(fingerprint) DO UPDATE SET index_name=excluded.index_name,spec=excluded.spec,definition_hash=excluded.definition_hash,plan_hash=excluded.plan_hash,applied_at=now()",
        spec.fingerprint,
        row["name"],
        json.dumps(spec.to_dict()),
        definition_digest(row["definition"]),
        plan_hash,
    )


async def _limit_build_workspace(
    conn, artifact, rows, estimate, *, required=False, addition=True
):
    """Bound PostgreSQL scratch files within the reviewed peak-space allowance."""
    rows = list(rows)
    retiring = {op["name"] for op in artifact["operations"] if op["kind"] == "drop"}
    steady_rows = [row for row in rows if row["name"] not in retiring]
    steady_estimate = estimate if addition else 0
    if not required and (
        len(steady_rows) + int(addition) > artifact["budgets"]["max_indexes"]
        or sum(row["bytes"] for row in steady_rows) + steady_estimate
        > artifact["budgets"]["max_index_bytes"]
    ):
        raise PlanError("Steady-state budget blocks further performance provisioning")
    remaining = (
        artifact["budgets"]["max_build_bytes"]
        - sum(r["bytes"] for r in rows)
        - estimate
    )
    if remaining < 0:
        raise PlanError("Temporary build-space budget exceeded")
    if not await conn.fetchval(
        "SELECT has_parameter_privilege(current_user,'temp_file_limit','SET')"
    ):
        raise PlanError("Migration role requires SET privilege on temp_file_limit")
    await conn.execute(f"SET temp_file_limit='{remaining // 1024}kB'")


async def apply(conn, artifact, *, approved_hash, expected_specs=None):
    """Apply exactly one approved artifact with drift checks and resumable progress."""
    if conn.is_in_transaction():
        raise PlanError("Apply requires a dedicated autocommit connection")
    specs = validate_plan(artifact)
    if approved_hash != artifact["hash"]:
        raise PlanError("Approval must name the exact reviewed plan hash")
    if (
        expected_specs is not None
        and catalog.revision(expected_specs) != artifact["catalog_revision"]
    ):
        raise PlanError("Source catalog changed after planning")
    schema, plan_hash = artifact["target"]["schema"], artifact["hash"]
    if await target(conn, schema) != artifact["target"]:
        raise PlanError("Plan targets another database")
    if not await conn.fetchval(
        "SELECT pg_try_advisory_lock(hashtext(current_database()),hashtext('integral:indexes'))"
    ):
        raise PlanError("Another index migration owns this database")
    completed, active = [], None
    limited_build_workspace = False
    try:
        await _control_schema(conn, schema)
        prior = await conn.fetchrow(
            f"SELECT * FROM {q(schema)}.integral_index_migrations WHERE plan_hash=$1",
            plan_hash,
        )
        if prior:
            completed = (
                json.loads(prior["completed_ops"])
                if isinstance(prior["completed_ops"], str)
                else list(prior["completed_ops"])
            )
            active = prior["active_op"]
        rows = await inventory(conn, schema)
        actual = {r["name"]: r for r in rows}
        own = {
            op.get(
                "index_name", IndexSpec.from_dict(op["spec"]).name
            ): IndexSpec.from_dict(op["spec"])
            for i, op in enumerate(artifact["operations"])
            if op["kind"] in {"create", "register", "repair"}
            and (i in completed or i == active)
        }
        dropped = {
            op["name"]
            for i, op in enumerate(artifact["operations"])
            if op["kind"] == "drop" and (i in completed or i == active)
        }
        completed_drops = {
            op["name"]
            for number, op in enumerate(artifact["operations"])
            if op["kind"] == "drop" and number in completed
        }
        active_drop = next(
            (
                op["name"]
                for number, op in enumerate(artifact["operations"])
                if op["kind"] == "drop" and number == active
            ),
            None,
        )
        schema_started = bool(completed or active is not None)
        schema_primaries = {
            spec.table: spec
            for spec in catalog.baseline_specs(schema)
            if spec.logical_id.endswith(":primary")
        }
        if artifact["include_search"]:
            from .search import search_specs

            schema_primaries.update(
                (spec.table, spec)
                for spec in search_specs(schema=schema)
                if spec.logical_id.endswith(":primary")
            )
        for name, expected in artifact["starting_inventory"].items():
            if name in dropped and name not in actual:
                continue
            if name in completed_drops:
                raise PlanError("A retired index reappeared after completion")
            if name == active_drop and name in actual:
                interrupted = actual[name]
                if interrupted["definition"] == expected["definition"] and not (
                    interrupted["primary"]
                    or interrupted["unique"]
                    or interrupted["constraint_backed"]
                ):
                    # DROP CONCURRENTLY can durably mark an index invalid before
                    # a crash. Its recorded active intent authorizes finishing
                    # this exact drop, never dropping a changed or unique index.
                    continue
            if (
                name in own
                and name in actual
                and await definition_matches(conn, own[name], actual[name])
            ):
                continue
            if name not in actual or snapshot([actual[name]])[name] != expected:
                raise PlanError("Existing index catalog changed after review")
        for name, row in actual.items():
            controls = {s.table + "_pkey": s for s in catalog.control_specs(schema)}
            if name in controls and await definition_matches(conn, controls[name], row):
                continue
            if name in artifact["starting_inventory"]:
                continue
            if name in own and await definition_matches(conn, own[name], row):
                continue
            if (
                schema_started
                and row["primary"]
                and name == row["table_name"] + "_pkey"
                and row["table_name"] in schema_primaries
                and await definition_matches(
                    conn, schema_primaries[row["table_name"]], row
                )
            ):
                continue
            raise PlanError("Unexpected index appeared after review")
        await conn.execute(
            f"INSERT INTO {q(schema)}.integral_index_migrations (plan_hash,artifact,state) VALUES ($1,$2::jsonb,'running') ON CONFLICT(plan_hash) DO UPDATE SET state='running',error_type=NULL,updated_at=now()",
            plan_hash,
            json.dumps(artifact),
        )
        await conn.execute("SET lock_timeout='5s'")
        await conn.execute("SET statement_timeout='30min'")
        for number, op in enumerate(artifact["operations"]):
            if number in completed:
                continue
            await conn.execute(
                f"UPDATE {q(schema)}.integral_index_migrations SET active_op=$2,updated_at=now() WHERE plan_hash=$1",
                plan_hash,
                number,
            )
            if op["kind"] == "schema":
                await conn.execute(op["sql"])
            elif op["kind"] in {"create", "register", "repair"}:
                spec = IndexSpec.from_dict(op["spec"])
                index_name = op.get("index_name", spec.name)
                current = {r["name"]: r for r in await inventory(conn, schema)}
                if (
                    op["kind"] == "repair"
                    and index_name in current
                    and not await definition_matches(conn, spec, current[index_name])
                ):
                    if not await definition_matches(
                        conn,
                        spec,
                        {**current[index_name], "valid": True, "ready": True},
                    ):
                        raise PlanError("Repair definition changed")
                    await _limit_build_workspace(
                        conn,
                        artifact,
                        current.values(),
                        op["estimated_bytes"],
                        required=spec.required,
                        addition=False,
                    )
                    limited_build_workspace = True
                    await conn.execute(op["sql"])
                    current = {r["name"]: r for r in await inventory(conn, schema)}
                if index_name in current and not await definition_matches(
                    conn, spec, current[index_name]
                ):
                    raise PlanError(
                        "Invalid or conflicting index requires a separately reviewed repair"
                    )
                if index_name not in current:
                    if op["kind"] == "register":
                        raise PlanError("Reviewed adoption index disappeared")
                    await _limit_build_workspace(
                        conn,
                        artifact,
                        current.values(),
                        op["estimated_bytes"],
                        required=spec.required,
                    )
                    limited_build_workspace = True
                    await conn.execute(op["sql"])
                current = {r["name"]: r for r in await inventory(conn, schema)}
                await _receipt(conn, schema, spec, current[index_name], plan_hash)
            elif op["kind"] == "drop":
                current = {r["name"]: r for r in await inventory(conn, schema)}
                if op["name"] in current:
                    row = current[op["name"]]
                    if (
                        row["definition"] != op["expected_definition"]
                        or row["primary"]
                        or row["unique"]
                        or row["constraint_backed"]
                    ):
                        raise PlanError("Removal precondition changed")
                    receipts = await registered(conn, schema)
                    if any(fp not in receipts for fp in op["replacement_fingerprints"]):
                        raise PlanError("Replacement coverage is not applied")
                    await conn.execute(op["sql"])
                await conn.execute(
                    f"DELETE FROM {q(schema)}.integral_index_catalog WHERE index_name=$1",
                    op["name"],
                )
            completed.append(number)
            await conn.execute(
                f"UPDATE {q(schema)}.integral_index_migrations SET completed_ops=$2::jsonb,active_op=NULL,updated_at=now() WHERE plan_hash=$1",
                plan_hash,
                json.dumps(completed),
            )
        role = artifact.get("runtime_role")
        if role:
            if not await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname=$1", role):
                raise PlanError("Provision the runtime role before applying this plan")
            await conn.execute(f"REVOKE CREATE ON SCHEMA {q(schema)} FROM PUBLIC")
            await conn.execute(f"REVOKE ALL ON SCHEMA {q(schema)} FROM {q(role)}")
            await conn.execute(f"GRANT USAGE ON SCHEMA {q(schema)} TO {q(role)}")
            tables = await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname=$1", schema
            )
            data_tables = {s.table for s in specs} | {"object", "node", "edge"}
            metadata_tables = {"integral_index_catalog", "integral_index_migrations"}
            for table in tables:
                if table["tablename"] not in data_tables | metadata_tables:
                    continue
                grants = (
                    "SELECT"
                    if table["tablename"] in metadata_tables
                    else "SELECT,INSERT,UPDATE,DELETE"
                )
                await conn.execute(
                    f"GRANT {grants} ON {q(schema)}.{q(table['tablename'])} TO {q(role)}"
                )
        rows = await inventory(conn, schema)
        if (
            len(rows) > artifact["budgets"]["max_indexes"]
            or sum(r["bytes"] for r in rows) > artifact["budgets"]["max_index_bytes"]
        ):
            raise PlanError("Applied catalog exceeds its steady-state budget")
        await conn.execute(
            f"UPDATE {q(schema)}.integral_index_migrations SET state='complete',active_op=NULL,updated_at=now() WHERE plan_hash=$1",
            plan_hash,
        )
        return {
            "state": "complete",
            "plan_hash": plan_hash,
            "operations": len(completed),
        }
    except BaseException as exc:
        try:
            await conn.execute(
                f"UPDATE {q(schema)}.integral_index_migrations SET state='failed',error_type=$2,updated_at=now() WHERE plan_hash=$1",
                plan_hash,
                type(exc).__name__,
            )
        except Exception:
            pass
        raise
    finally:
        await conn.execute("RESET lock_timeout")
        await conn.execute("RESET statement_timeout")
        if limited_build_workspace:
            await conn.execute("RESET temp_file_limit")
        await conn.execute(
            "SELECT pg_advisory_unlock(hashtext(current_database()),hashtext('integral:indexes'))",
        )
