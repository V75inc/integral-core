"""DB-backed entry listing for list/feed endpoints."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from app.models.nodes import Entry, Track
from app.services.entry_search import entry_search_query_clause
from app.services.pagination import (
    DEFAULT_ENTITY_SORT,
    MAX_LIMIT,
    _context_field_query_path,
    _merge_query_clauses,
)
from app.services.query_filters import FILTER_OPS, canonical_filter_op
from app.services.relative_date_filters import resolve_relative_date


def tag_filter_clauses(
    tag_id: Optional[str], tag_ids: Optional[str]
) -> List[Dict[str, Any]]:
    """Match every selected tag before pagination; retain single-tag clients."""
    from app.api.errors import BadRequestError

    if len(tag_ids or "") > 4096:
        raise BadRequestError(message="Tag filters are too long")
    ids = list(
        dict.fromkeys(
            value.strip()
            for value in [tag_id or "", *(tag_ids or "").split(",")]
            if value.strip()
        )
    )
    if len(ids) > 20:
        raise BadRequestError(message="Select at most 20 tag filters")
    return [{"field": "tags", "operator": "eq", "value": ids}] if ids else []


def _resolve_filter_value(value: Any) -> Any:
    if isinstance(value, list):
        return [resolve_relative_date(item) for item in value]
    return resolve_relative_date(value)


def _view_filter_to_clause(
    field: str, operator: str, value: Any
) -> Optional[Dict[str, Any]]:
    path = _context_field_query_path(field)
    op = canonical_filter_op(operator)
    if op not in FILTER_OPS:
        return None
    value = _resolve_filter_value(value)
    if path == "context.tags" and op in ("eq", "contains"):
        # Entry.tags is an id array: equality means membership, not array equality.
        return {path: {"$all": value if isinstance(value, list) else [value]}}
    if op == "eq":
        return {path: value}
    if op == "neq":
        return {path: {"$ne": value}}
    if op == "in":
        return {path: {"$in": value if isinstance(value, list) else [value]}}
    if op == "not_in":
        return {path: {"$nin": value if isinstance(value, list) else [value]}}
    if op == "contains":
        if isinstance(value, str):
            return {path: {"$regex": value, "$options": "i"}}
        return {path: value}
    if op == "gt":
        return {path: {"$gt": value}}
    if op == "lt":
        return {path: {"$lt": value}}
    if op == "gte":
        return {path: {"$gte": value}}
    if op == "lte":
        return {path: {"$lte": value}}
    if op == "exists":
        if value is False:
            return {path: None}
        return {path: {"$exists": True}}
    return None


def view_filter_clauses(filters: Any) -> List[Dict[str, Any]]:
    """Translate a saved-view filter list. ``op`` and ``operator`` both apply."""
    clauses: List[Dict[str, Any]] = []
    for filt in filters or []:
        if not isinstance(filt, dict):
            continue
        field = filt.get("field") or ""
        if not field:
            continue
        operator = filt.get("op") or filt.get("operator") or "eq"
        clause = _view_filter_to_clause(field, str(operator), filt.get("value"))
        if clause:
            clauses.append(clause)
    return clauses


def _view_sort_to_db_sort(view_sort: List[Dict[str, Any]]) -> List[Tuple[str, int]]:
    if not view_sort:
        return list(DEFAULT_ENTITY_SORT)
    out: List[Tuple[str, int]] = []
    for spec in view_sort:
        field = spec.get("field", "updated_at")
        direction = spec.get("direction", "desc")
        path = _context_field_query_path(field)
        out.append(
            (path, -1 if str(direction).lower() in ("desc", "descending") else 1)
        )
    out.append(("id", -1 if out and out[0][1] < 0 else 1))
    return out


async def _resolve_view_type_ids(view_node: Any) -> Optional[Set[str]]:
    from app.models.edges import CONTAINS
    from app.services.app_graph import ensure_track_attached_operational_model
    from app.services.entry_type_resolver import canonical_entry_type_key
    from app.services.operational_model_runtime import (
        backfill_view_entry_type_constraints_from_manifest,
        slug_manifest_key,
    )

    track_id = getattr(view_node, "track_id", "") or ""
    if not track_id:
        return None
    track = await Track.get(track_id)
    if track is None:
        return None

    if not (getattr(view_node, "entry_type_keys", None) or []):
        await backfill_view_entry_type_constraints_from_manifest(track, [view_node])

    keys = list(getattr(view_node, "entry_type_keys", None) or [])
    if not keys:
        return None

    allowed_keys = {k for k in (slug_manifest_key(x) for x in keys) if k}
    if not allowed_keys:
        return set()

    cp = await ensure_track_attached_operational_model(track)
    if cp is None:
        return set()

    ets = await cp.nodes(edge=[CONTAINS], node=["EntryType"])
    return {
        et.id
        for et in ets
        if getattr(et, "id", None) and canonical_entry_type_key(et) in allowed_keys
    }


async def fetch_accessible_entries_page(
    user_id: str,
    *,
    track_id: Optional[str] = None,
    app_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    view_node: Any = None,
    filters: Optional[List[Dict[str, Any]]] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = 20,
    include_total: bool = False,
) -> Tuple[List[Entry], dict]:
    """Fetch one page of entries via DB keyset pagination."""
    from app.services.entry_access_projection import candidate_query, select_page

    if track_id:
        app_id = None
    scope = {"track_id": track_id, "app_id": app_id, "workspace_id": workspace_id}
    scope_query = await candidate_query(user_id, **scope)
    if scope_query is None:
        empty = 0 if include_total else None
        return [], {"total": empty, "next_cursor": None, "has_more": False}

    clauses: List[Dict[str, Any]] = []
    if scope_query:
        clauses.append(scope_query)

    if status:
        clauses.append({"context.status": status})

    if view_node is not None:
        type_ids = await _resolve_view_type_ids(view_node)
        if type_ids is not None:
            if not type_ids:
                empty = 0 if include_total else None
                return [], {"total": empty, "next_cursor": None, "has_more": False}
            clauses.append({"context.type_id": {"$in": list(type_ids)}})

        cfg = getattr(view_node, "config", None) or {}
        if isinstance(cfg, dict):
            clauses.extend(view_filter_clauses(cfg.get("filters")))

    if filters:
        clauses.extend(view_filter_clauses(filters))

    search_clause = entry_search_query_clause(q)
    if search_clause:
        clauses.append(search_clause)

    query = _merge_query_clauses(*clauses)
    sort = list(DEFAULT_ENTITY_SORT)
    if view_node is not None:
        cfg = getattr(view_node, "config", None) or {}
        if isinstance(cfg, dict) and cfg.get("sort"):
            sort = _view_sort_to_db_sort(cfg.get("sort") or [])

    return await select_page(
        user_id,
        query,
        cursor=cursor,
        limit=min(max(1, limit), MAX_LIMIT),
        sort=sort,
        include_total=include_total,
        **scope,
    )
