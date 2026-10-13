"""Bounded dashboard projection of App-owned declarative record queries.

Only the existing declaration is executed. Dashboard scope can narrow its
visible rows, never expand the App query or replace its authorization.
"""

from __future__ import annotations

from typing import Any, Dict, List

_SCALAR_TYPES = {
    "text": "string",
    "textarea": "string",
    "email": "string",
    "url": "string",
    "date": "string",
    "datetime": "string",
    "select": "string",
    "number": "number",
    "integer": "integer",
    "boolean": "boolean",
    "checkbox": "boolean",
}


def qualify_template(
    query: Dict[str, Any], fields: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Describe only fields selected by an eligible portable query template."""
    from app.schemas.query_spec import QuerySpec

    if query.get("dashboard") is False:
        return query
    if isinstance(query.get("dashboard"), dict):
        return query
    template = query.get("query_template")
    if not isinstance(template, dict):
        return query
    try:
        spec = QuerySpec.model_validate(template)
    except ValueError:
        return query
    inputs = query.get("input_schema") or {}
    if (
        spec.resource != "entry"
        or spec.traversal
        or template.get("cursor")
        or set(inputs.get("properties") or {}) - {"cursor"}
        or inputs.get("required")
        or "cursor" not in (inputs.get("properties") or {})
    ):
        return query
    selected = set(spec.select or [])
    if not {"id", "track_id", "title"} <= selected:
        return query
    properties: Dict[str, Any] = {
        key: {"type": "string"}
        for key in selected
        if key in {"id", "track_id", "title", "created_at", "updated_at"}
    }
    custom = {}
    for field in fields:
        key, kind = field["key"], _SCALAR_TYPES.get(field["type"])
        if kind and ("custom_fields" in selected or f"custom_fields.{key}" in selected):
            custom[key] = {"type": [kind, "null"]}
    properties["custom_fields"] = {"type": "object", "properties": custom}
    return {
        **query,
        "_template_dashboard": True,
        "dashboard": {
            "rows_path": "items",
            "total_path": "total_estimate",
            "params": {},
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"type": "object", "properties": properties},
                },
                "total_estimate": {"type": "integer"},
            },
        },
    }


async def collect_template_rows(
    *,
    invoke,
    user_id: str,
    workspace_id: str,
    app_id: str,
    query_key: str,
    track_id: str,
    budget: int,
) -> Dict[str, Any]:
    """Complete a bounded authorized query before producing any exact metric."""
    rows, identities, cursors = [], set(), set()
    params: Dict[str, Any] = {}
    for _ in range(100):
        result = await invoke(
            user_id=user_id,
            workspace_id=workspace_id,
            app_id=app_id,
            query_key=query_key,
            params=params,
        )
        output = result.get("output") or {}
        page = output.get("items")
        if not isinstance(page, list) or any(not isinstance(row, dict) for row in page):
            return {"error": "incomplete_declared_query"}
        for row in page:
            identity = row.get("id")
            if not isinstance(identity, str) or not identity or identity in identities:
                return {"error": "duplicate_declared_query_rows"}
            identities.add(identity)
            if len(identities) > budget:
                return {"error": "over_budget", "budget": budget}
            if row.get("track_id") == track_id:
                rows.append(row)
        cursor = output.get("next_cursor")
        if cursor is None:
            return {"items": rows, "total_estimate": len(rows)}
        if not isinstance(cursor, str) or not cursor or cursor in cursors or not page:
            return {"error": "incomplete_declared_query"}
        cursors.add(cursor)
        params = {"cursor": cursor}
    return {"error": "over_budget", "budget": budget}
