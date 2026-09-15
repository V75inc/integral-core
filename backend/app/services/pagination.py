"""Cursor-based pagination utility for Integral list endpoints.

Implements architecture Section 10.3: all list endpoints support cursor-based
pagination with ``cursor`` + ``limit`` params, returning ``next_cursor``,
``has_more``, and optional ``total``.

List endpoints MUST paginate at the database layer via
:func:`paginate_entity_find` — never load full collections into Python for
slicing. Legacy :func:`paginate_list` / :func:`build_paginated_response` remain
for audit/event envelopes that are not yet DB-keyset backed.
"""

import base64
import json
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Type, TypeVar

from jvspatial.core import Object

logger = logging.getLogger(__name__)

# Unbound — used by the generic in-memory pagination helpers (paginate_list,
# build_paginated_response, paginate_query), which accept both jvspatial
# Object/Node lists and plain envelope dataclasses (e.g. ChangeEventEnvelope,
# explicitly NOT a Node; see change_event_logger.py). Nothing in those
# helpers needs Object-ness, only key_fn(item).
T = TypeVar("T")

# Bound to Object — used by the DB-keyset helpers (paginate_entity_find,
# paginate_nodes_by_ids), which call Object-only GraphContext methods
# (ensure_indexes, _deserialize_entity, _build_database_query).
TNode = TypeVar("TNode", bound=Object)

MAX_LIMIT = 100

DEFAULT_ENTITY_SORT: List[Tuple[str, int]] = [
    ("context.updated_at", -1),
    ("id", -1),
]


def encode_cursor(node_id: str, timestamp: str) -> str:
    """Encode a node ID + timestamp into an opaque cursor string."""
    payload = json.dumps({"id": node_id, "ts": timestamp}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_cursor(cursor: str) -> Tuple[Optional[str], Optional[str]]:
    """Decode a cursor string into (node_id, timestamp). Returns (None, None) on error."""
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        return payload.get("id"), payload.get("ts")
    except Exception:
        return None, None


def paginate_list(
    items: List[T],
    cursor: Optional[str],
    limit: int,
    key_fn: Callable[[T], Tuple[str, str]],
) -> Tuple[List[T], Optional[str], bool]:
    """Apply cursor pagination to an already-sorted list of items.

    Args:
        items: Full sorted list (newest first).
        cursor: Opaque cursor from a previous response, or None to start from the beginning.
        limit: Maximum items to return (capped at MAX_LIMIT).
        key_fn: Callable that returns (id, timestamp) for a given item.

    Returns:
        (page_items, next_cursor, has_more)
    """
    limit = min(max(1, limit), MAX_LIMIT)

    start_index = 0
    if cursor:
        cursor_id, cursor_ts = decode_cursor(cursor)
        if cursor_id:
            for i, item in enumerate(items):
                iid, _ = key_fn(item)
                if iid == cursor_id:
                    start_index = i + 1
                    break

    page = items[start_index : start_index + limit]
    has_more = start_index + limit < len(items)

    next_cursor: Optional[str] = None
    if has_more and page:
        last_id, last_ts = key_fn(page[-1])
        next_cursor = encode_cursor(last_id, last_ts)

    return page, next_cursor, has_more


def node_key(node) -> Tuple[str, str]:
    """Default key function for jvspatial nodes: (id, updated_at or created_at)."""
    ts = getattr(node, "updated_at", None) or getattr(node, "created_at", None) or ""
    return node.id, str(ts)


def entity_sort_key(node: Any, sort: List[Tuple[str, int]]) -> Tuple[str, str]:
    """Cursor tuple for the first sort field + id tiebreaker."""
    if not sort:
        return node_key(node)
    field, _direction = sort[0]
    if field == "id":
        val = getattr(node, "id", "") or ""
    elif field.startswith("context."):
        attr = field.split(".", 1)[1]
        val = getattr(node, attr, None)
        if val is None and hasattr(node, "context"):
            ctx = getattr(node, "context", None)
            if isinstance(ctx, dict):
                val = ctx.get(attr)
    else:
        val = getattr(node, field, None)
    return node.id, str(val if val is not None else "")


def _context_field_query_path(field: str) -> str:
    """Map a view/API field name to a jvspatial query path."""
    if field.startswith("context."):
        return field
    return f"context.{field}"


def _merge_query_clauses(*clauses: Dict[str, Any]) -> Dict[str, Any]:
    parts = [c for c in clauses if c]
    if not parts:
        return {}
    if len(parts) == 1:
        return parts[0]
    return {"$and": parts}


def _keyset_clause(
    cursor: str,
    sort: List[Tuple[str, int]],
) -> Optional[Dict[str, Any]]:
    cursor_id, cursor_val = decode_cursor(cursor)
    if not cursor_id or cursor_val is None:
        return None
    ts_field = sort[0][0] if sort else "context.updated_at"
    desc = (sort[0][1] if sort else -1) < 0
    op = "$lt" if desc else "$gt"
    id_op = "$lt" if desc else "$gt"
    return {
        "$or": [
            {ts_field: {op: cursor_val}},
            {ts_field: cursor_val, "id": {id_op: cursor_id}},
        ]
    }


async def paginate_entity_find(
    entity_cls: Type[TNode],
    query: Dict[str, Any],
    cursor: Optional[str],
    limit: int,
    *,
    sort: Optional[List[Tuple[str, int]]] = None,
    include_total: bool = False,
) -> Tuple[List[TNode], dict]:
    """Keyset pagination via jvspatial ``database.find(limit=, sort=)``."""
    from jvspatial.core.context import get_default_context

    limit = min(max(1, limit), MAX_LIMIT)
    effective_sort = sort or DEFAULT_ENTITY_SORT
    page_query = dict(query) if query else {}
    if cursor:
        keyset = _keyset_clause(cursor, effective_sort)
        if keyset:
            page_query = _merge_query_clauses(page_query, keyset)

    ctx = get_default_context()
    await ctx.ensure_indexes(entity_cls)
    collection, final_query = await entity_cls._build_database_query(
        ctx, page_query, {}
    )
    rows = await ctx.database.find(
        collection,
        final_query,
        sort=effective_sort,
        limit=limit + 1,
    )
    objects: List[TNode] = []
    for data in rows:
        obj = await ctx._deserialize_entity(entity_cls, data)
        if obj:
            objects.append(obj)

    has_more = len(objects) > limit
    page = objects[:limit]
    next_cursor: Optional[str] = None
    if has_more and page:
        last_id, last_val = entity_sort_key(page[-1], effective_sort)
        next_cursor = encode_cursor(last_id, last_val)

    total: Optional[int] = None
    if include_total:
        base_query = dict(query) if query else {}
        count_collection, count_filter = await entity_cls._build_database_query(
            ctx, base_query, {}
        )
        total = await ctx.database.count(count_collection, count_filter)

    return page, {
        "total": total,
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


async def paginate_nodes_by_ids(
    entity_cls: Type[TNode],
    node_ids: List[str],
    cursor: Optional[str],
    limit: int,
    *,
    sort: Optional[List[Tuple[str, int]]] = None,
    include_total: bool = False,
) -> Tuple[List[TNode], dict]:
    """DB pagination over an allow-list of node ids (permission pre-filter)."""
    if not node_ids:
        return [], {
            "total": 0 if include_total else None,
            "next_cursor": None,
            "has_more": False,
        }
    return await paginate_entity_find(
        entity_cls,
        {"id": {"$in": node_ids}},
        cursor,
        limit,
        sort=sort,
        include_total=include_total,
    )


def build_paginated_response(
    items: List[T],
    cursor: Optional[str],
    limit: int,
    key_fn: Callable[[T], Tuple[str, str]],
    *,
    include_total: bool = True,
) -> Tuple[List[T], dict]:
    """Return (page_items, base_response_dict) using cursor pagination (Section 10.3)."""
    page_items, next_cursor, has_more = paginate_list(items, cursor, limit, key_fn)
    return page_items, {
        "total": len(items) if include_total else None,
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


async def paginate_query(
    query_fn: Callable[[Optional[str], int], Awaitable[List[T]]],
    cursor: Optional[str],
    limit: int,
    key_fn: Callable[[T], Tuple[str, str]],
    *,
    include_total: bool = False,
    total_fn: Optional[Callable[[], Awaitable[int]]] = None,
) -> Tuple[List[T], dict]:
    """Cursor pagination for DB-backed keyset/list queries.

    ``query_fn`` receives ``(cursor, limit_plus_one)`` and should return
    at most ``limit + 1`` rows in already-sorted order.
    """
    limit = min(max(1, limit), MAX_LIMIT)
    rows = await query_fn(cursor, limit + 1)
    has_more = len(rows) > limit
    page = rows[:limit]

    next_cursor: Optional[str] = None
    if has_more and page:
        last_id, last_ts = key_fn(page[-1])
        next_cursor = encode_cursor(last_id, last_ts)

    total: Optional[int] = None
    if include_total and total_fn is not None:
        total = await total_fn()

    return page, {
        "total": total,
        "next_cursor": next_cursor,
        "has_more": has_more,
    }
