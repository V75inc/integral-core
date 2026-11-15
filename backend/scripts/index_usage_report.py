#!/usr/bin/env python3
"""Read-only report of every index on the object, node and edge tables.

For each index: scan count since the statistics were last reset, size, the record
kind (entity) it serves, and its definition; then totals of the unused ones. Nothing
is created, changed or dropped.

    python scripts/index_usage_report.py --dsn postgresql://user:pw@host:5432/db [--schema public] [--json]

Statistics are per server and reset on a restore or ``pg_stat_reset()``: judge
"unused" only after a representative period of real traffic.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from typing import Any, Dict, List

TABLES = ("object", "node", "edge")

_QUERY = """
SELECT s.relname AS table_name,
       s.indexrelname AS index_name,
       s.idx_scan AS scans,
       pg_relation_size(s.indexrelid) AS size_bytes,
       pg_get_indexdef(s.indexrelid) AS definition,
       i.indisunique AS is_unique,
       i.indisprimary AS is_primary
FROM pg_stat_user_indexes s
JOIN pg_index i ON i.indexrelid = s.indexrelid
WHERE s.schemaname = $1 AND s.relname = ANY($2::text[])
ORDER BY pg_relation_size(s.indexrelid) DESC
"""

_ENTITY_LITERAL = re.compile(r"entity\s*=\s*'([A-Za-z0-9_]+)'")


def served_entity(definition: str) -> str:
    """The record kind an index is limited to, 'any kind' when it covers all rows.

    A partial index (``WHERE entity = 'X'``) serves one kind. An index whose first
    key is the entity column serves every kind, but only in key order; one without
    it is read for every kind.
    """
    where = definition.split(" WHERE ", 1)
    if len(where) == 2:
        match = _ENTITY_LITERAL.search(where[1])
        if match:
            return match.group(1)
    return "any kind"


def classify(row: Dict[str, Any]) -> Dict[str, Any]:
    """One database row as a report row: counts, size, kind served, partial or not."""
    definition = row["definition"]
    return {
        "table": row["table_name"],
        "index": row["index_name"],
        "scans": int(row["scans"]),
        "size_bytes": int(row["size_bytes"]),
        "unique": bool(row["is_unique"]),
        "primary": bool(row["is_primary"]),
        "entity": served_entity(definition),
        "partial": " WHERE " in definition,
        "definition": definition,
    }


def summarise(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-table totals: indexes, bytes, never scanned (not unique or primary), partial."""
    out: Dict[str, Any] = {}
    for table in TABLES:
        mine = [r for r in rows if r["table"] == table]
        unused = [
            r for r in mine if r["scans"] == 0 and not r["unique"] and not r["primary"]
        ]
        out[table] = {
            "indexes": len(mine),
            "bytes": sum(r["size_bytes"] for r in mine),
            "unused": len(unused),
            "unused_bytes": sum(r["size_bytes"] for r in unused),
            "partial": sum(1 for r in mine if r["partial"]),
        }
    return out


def human(n: int) -> str:
    """Bytes as a short readable size."""
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{n} B"


def budget_violations(
    rows: List[Dict[str, Any]],
    max_indexes: int | None = None,
    max_bytes: int | None = None,
) -> List[str]:
    """Read-only deployment budgets; never infer that an index may be dropped."""
    violations = []
    for table, totals in summarise(rows).items():
        if max_indexes is not None and totals["indexes"] > max_indexes:
            violations.append(
                f"{table}: {totals['indexes']} indexes exceeds budget {max_indexes}"
            )
        if max_bytes is not None and totals["bytes"] > max_bytes:
            violations.append(
                f"{table}: {totals['bytes']} index bytes exceeds budget {max_bytes}"
            )
    return violations


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("Budget must be a positive integer")
    return parsed


async def fetch(dsn: str, schema: str) -> List[Dict[str, Any]]:
    """Read the index statistics from the database (read only)."""
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        records = await conn.fetch(_QUERY, schema, list(TABLES))
    finally:
        await conn.close()
    return [classify(dict(r)) for r in records]


def render(rows: List[Dict[str, Any]]) -> str:
    """The text report: totals per table, then every index by size."""
    lines = []
    totals = summarise(rows)
    for table in TABLES:
        t = totals[table]
        lines.append(
            f"{table}: {t['indexes']} indexes ({human(t['bytes'])}), "
            f"{t['unused']} never scanned ({human(t['unused_bytes'])}), {t['partial']} partial"
        )
    lines.append("")
    lines.append(f"{'table':6} {'scans':>9} {'size':>9}  {'serves':22} index")
    for r in rows:
        lines.append(
            f"{r['table']:6} {r['scans']:>9} {human(r['size_bytes']):>9}  {r['entity'][:22]:22} {r['index']}"
        )
    return "\n".join(lines)


def main(argv: List[str] | None = None) -> int:
    """Command line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--schema", default="public")
    parser.add_argument(
        "--json", action="store_true", help="print JSON instead of text"
    )
    parser.add_argument("--max-indexes-per-table", type=positive_int)
    parser.add_argument("--max-index-bytes-per-table", type=positive_int)
    args = parser.parse_args(argv)
    rows = asyncio.run(fetch(args.dsn, args.schema))
    violations = budget_violations(
        rows, args.max_indexes_per_table, args.max_index_bytes_per_table
    )
    if args.json:
        print(
            json.dumps(
                {
                    "summary": summarise(rows),
                    "indexes": rows,
                    "budget_violations": violations,
                },
                indent=2,
            )
        )
    else:
        print(render(rows))
        for violation in violations:
            print(f"Budget exceeded: {violation}")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
