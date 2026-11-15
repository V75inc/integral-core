"""Benchmark-only candidate-reader comparison. Never imported by application code.

Only handles one bounded, ID-ordered Entry window; every other read delegates
unchanged. Core's canonical admission/hydration still run on both sides.
"""

import json
import re


def content_free_plan(value):
    """Keep plan shape/costs/buffers; expressions may contain SQL parameters."""
    if isinstance(value, list):
        return [content_free_plan(item) for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return {
            key: content_free_plan(item)
            for key, item in value.items()
            if isinstance(item, (dict, list, int, float, bool))
            or key in {"Node Type", "Parent Relationship", "Sort Method"}
        }
    return value


class ParameterizedCandidateProbe:
    def __init__(self, database):
        self.database = database
        self.original = database.find
        self.calls = 0
        self.statement = None
        self.parameters = None

    async def find(self, collection, query, *, limit=None, sort=None):
        if collection != "node" or sort != [("id", 1)] or not limit or limit > 52:
            return await self.original(collection, query, limit=limit, sort=sort)
        clauses = []

        def flatten(value):
            for key, item in value.items():
                if key == "$and":
                    for nested in item:
                        flatten(nested)
                else:
                    clauses.append((key, item))

        flatten(query)
        if (
            not any(key == "context.track_id" for key, _ in clauses)
            or ("entity", "Entry") not in clauses
        ):
            return await self.original(collection, query, limit=limit, sort=sort)
        columns = {
            "entity": "entity",
            "id": "id",
            "context.track_id": "data->'context'->>'track_id'",
            "context.type_id": "data->'context'->>'type_id'",
        }
        conditions, arguments = [], []
        for field, value in clauses:
            if field not in columns:
                return await self.original(collection, query, limit=limit, sort=sort)
            column = columns[field]
            if isinstance(value, str):
                arguments.append(value)
                conditions.append(f"({column}) = ${len(arguments)}")
            elif isinstance(value, dict) and set(value) == {"$in"}:
                arguments.append(value["$in"])
                conditions.append(f"({column}) = ANY(${len(arguments)}::text[])")
            else:
                return await self.original(collection, query, limit=limit, sort=sort)
        schema = self.database.schema_name
        assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema)
        arguments.append(limit)
        sql = (
            f"SELECT data FROM {schema}.node WHERE "
            + " AND ".join(conditions)
            + f" ORDER BY id ASC LIMIT ${len(arguments)}"
        )
        self.statement, self.parameters = sql, arguments
        self.calls += 1
        async with self.database._acquire_conn() as connection:
            rows = await connection.fetch(sql, *arguments)
        return [self.database._record_from_row(row) for row in rows]

    async def explain(self):
        assert self.calls > 0, "Comparison did not exercise its candidate reader"
        async with self.database._acquire_conn() as connection:
            result = await connection.fetchval(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + self.statement,
                *self.parameters,
            )
        return content_free_plan(
            json.loads(result) if isinstance(result, str) else result
        )
