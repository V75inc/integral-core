"""Read-only physical catalog inspection and conservative definition matching."""

import hashlib
import json
import re


async def inventory(conn, schema="public"):
    """Inspect actual physical definitions, validity, ordering and storage cost."""
    rows = await conn.fetch(
        """
        SELECT t.relname AS table_name, x.relname AS name,
               pg_get_indexdef(i.indexrelid) AS definition,
               pg_get_expr(i.indpred,i.indrelid) AS predicate,
               am.amname AS method, i.indisunique AS unique, i.indisprimary AS primary,
               i.indnullsnotdistinct AS nulls_not_distinct, i.indnatts-i.indnkeyatts AS included_columns,
               i.indisvalid AS valid, i.indisready AS ready,
               EXISTS(SELECT 1 FROM pg_constraint c WHERE c.conindid=i.indexrelid) AS constraint_backed,
               ARRAY(SELECT pg_get_indexdef(i.indexrelid,k,true) FROM generate_series(1,i.indnkeyatts) k) AS keys,
               i.indoption::int2[] AS options, pg_relation_size(i.indexrelid) AS bytes,
               ARRAY(SELECT o.opcname FROM generate_series(0,i.indnkeyatts-1) k JOIN pg_opclass o ON o.oid=i.indclass[k]) AS opclasses,
               ARRAY(SELECT o.opcdefault FROM generate_series(0,i.indnkeyatts-1) k JOIN pg_opclass o ON o.oid=i.indclass[k]) AS opcdefaults,
               ARRAY(SELECT co.collname FROM generate_series(0,i.indnkeyatts-1) k LEFT JOIN pg_collation co ON co.oid=i.indcollation[k]) AS collations,
               coalesce(s.idx_scan,0) AS scans
        FROM pg_index i JOIN pg_class x ON x.oid=i.indexrelid
        JOIN pg_class t ON t.oid=i.indrelid JOIN pg_namespace n ON n.oid=t.relnamespace
        JOIN pg_am am ON am.oid=x.relam LEFT JOIN pg_stat_user_indexes s ON s.indexrelid=x.oid
        WHERE n.nspname=$1 ORDER BY x.relname
    """,
        schema,
    )
    return [dict(r) for r in rows]


def snapshot(rows):
    """Capture definition drift independently of changing usage and size statistics."""
    return {
        r["name"]: {k: r[k] for k in ("definition", "valid", "ready")} for r in rows
    }


def digest(value):
    """Compute an unambiguous content hash for review artifacts."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _outer(value):
    while value.startswith("(") and value.endswith(")"):
        depth = 0
        quote = None
        complete = True
        for pos, char in enumerate(value):
            if char in "'\"":
                quote = None if quote == char else char if quote is None else quote
            if quote:
                continue
            depth += (char == "(") - (char == ")")
            if depth == 0 and pos < len(value) - 1:
                complete = False
                break
        if not complete:
            break
        value = value[1:-1].strip()
    return value


def normalized(expression):
    """Only discard SQL formatting and implicit casts on text literals.

    False negatives require review; never strip expression casts, collations,
    operators or null semantics to guess equivalence.
    """
    value = _outer((expression or "").strip())
    value = re.sub(r"('(?:[^']|'')*')::text(?:\[\])?", r"\1", value)
    # PostgreSQL removes parentheses around a JSON-path atom inside function
    # arguments. Do not remove a function's own argument parentheses.
    value = re.sub(
        r"(?<![A-Za-z0-9_])\(\s*(data\s*#>>?\s*'(?:[^']|'')*')\s*\)", r"\1", value
    )
    # Parenthesization of conjunctions differs in pg_get_expr output.
    for operator in (" OR ", " AND "):
        depth, quoted, last, parts = 0, False, 0, []
        for i, char in enumerate(value):
            if char == "'":
                quoted = not quoted
            if not quoted:
                depth += (char == "(") - (char == ")")
                if depth == 0 and value[i : i + len(operator)].upper() == operator:
                    parts.append(value[last:i])
                    last = i + len(operator)
        if parts:
            return (
                "("
                + operator.join(normalized(part) for part in (*parts, value[last:]))
                + ")"
            )
    tokens = re.findall(
        r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|\s+|[^\s'\"]+", _outer(value)
    )
    return "".join(
        (
            t
            if t.startswith(("'", '"'))
            else re.sub(
                r"\bLIKE\b",
                "~~",
                re.sub(r"\bILIKE\b", "~~*", t, flags=re.I),
                flags=re.I,
            ).lower()
        )
        for t in tokens
        if not t.isspace()
    )


def key_parts(key):
    """Separate explicit SQL ordering from its comparison expression."""
    match = re.search(r"\s+(ASC|DESC)(?:\s+NULLS\s+(FIRST|LAST))?$", key, re.I)
    direction, nulls = (
        (match.group(1).upper(), match.group(2)) if match else ("ASC", None)
    )
    if match:
        key = key[: match.start()]
    return normalized(key), (1 if direction == "DESC" else 0) + (
        2
        if (nulls or ("FIRST" if direction == "DESC" else "LAST")).upper() == "FIRST"
        else 0
    )


def key_semantics(key):
    """Extract ordering, collation and operator-class comparison requirements."""
    order = key_parts(key)[1]
    key = re.sub(r"\s+(ASC|DESC)(?:\s+NULLS\s+(FIRST|LAST))?$", "", key, flags=re.I)
    opclass = re.search(r"\s+([A-Za-z_][A-Za-z0-9_]*_ops)$", key)
    if opclass:
        key = key[: opclass.start()]
    collation = re.search(r'\s+COLLATE\s+("[^"]+"|[A-Za-z_][A-Za-z0-9_]*)$', key, re.I)
    if collation:
        key = key[: collation.start()]
    return (
        normalized(key),
        order,
        collation.group(1).strip('"') if collation else None,
        opclass.group(1) if opclass else None,
    )


async def target(conn, schema):
    """Bind an artifact to one database identity and network endpoint."""
    row = await conn.fetchrow(
        "SELECT current_database() AS database, (SELECT oid::bigint FROM pg_database WHERE datname=current_database()) AS oid, inet_server_addr()::text AS host, inet_server_port() AS port"
    )
    return {**dict(row), "schema": schema}


def expression_sql(key):
    """Remove index-only syntax before read-only PostgreSQL expression comparison."""
    value = re.sub(r"\s+(ASC|DESC)(?:\s+NULLS\s+(FIRST|LAST))?$", "", key, flags=re.I)
    value = re.sub(r"\s+[A-Za-z_][A-Za-z0-9_]*_ops$", "", value)
    return re.sub(
        r'\s+COLLATE\s+("[^"]+"|[A-Za-z_][A-Za-z0-9_]*)$', "", value, flags=re.I
    )


async def definition_matches(conn, spec, row):
    """Use PostgreSQL's parser/deparser, never execute source expressions.

    Formatting heuristics cannot establish boolean/expression equivalence.
    EXPLAIN without ANALYZE produces canonical projection expressions while
    retaining explicit casts. Metadata separately binds ordering/opclass/collation.
    """
    if (
        row.get("nulls_not_distinct")
        or row.get("included_columns")
        or not row["valid"]
        or not row["ready"]
        or row["method"] != spec.method
        or row["unique"] != spec.unique
        or row["table_name"] != spec.table
        or len(spec.keys) != len(row["keys"])
    ):
        return False
    for i, key in enumerate(spec.keys):
        _, ordering, collation, opclass = key_semantics(key)
        if spec.method == "btree" and ordering != row["options"][i]:
            return False
        if (
            row["collations"][i] if row["collations"][i] != "default" else None
        ) != collation:
            return False
        if opclass and row["opclasses"][i] != opclass:
            return False
        if not opclass and not row["opcdefaults"][i]:
            return False
    from jvspatial.db.indexes import identifier

    expected = [expression_sql(k) for k in spec.keys] + [spec.predicate or "true"]
    actual = [expression_sql(k) for k in row["keys"]] + [row["predicate"] or "true"]
    result = await conn.fetchval(
        "EXPLAIN (VERBOSE, FORMAT JSON, COSTS FALSE) SELECT "
        + ",".join(expected + actual)
        + f" FROM {identifier(spec.schema)}.{identifier(spec.table)} LIMIT 0"
    )
    parsed = json.loads(result) if isinstance(result, str) else result
    output = parsed[0]["Plan"].get("Output", [])
    return (
        len(output) == 2 * len(expected)
        and output[: len(expected)] == output[len(expected) :]
    )


async def runtime_privileges(conn, schema):
    """Detect ownership and persistent DDL authority on a serving login."""
    return bool(
        await conn.fetchval(
            """
        SELECT r.rolsuper OR r.rolcreatedb OR r.rolcreaterole
          OR has_database_privilege(current_user,current_database(),'CREATE')
          OR has_schema_privilege(current_user,$1,'CREATE')
          OR EXISTS(SELECT 1 FROM pg_namespace n WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema' AND has_schema_privilege(current_user,n.oid,'CREATE'))
          OR EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname=$1 AND c.relkind IN ('r','p') AND pg_has_role(current_user,c.relowner,'MEMBER'))
        FROM pg_roles r WHERE r.rolname=current_user
    """,
            schema,
        )
    )
