"""DB-backed entry listing for list/feed endpoints."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Set, Tuple

from app.models.nodes import App, Entry, Track
from app.services.entry_search import entry_search_query_clause
from app.services.pagination import (
    DEFAULT_ENTITY_SORT,
    _context_field_query_path,
    _merge_query_clauses,
    paginate_entity_find,
)
from app.services.permissions import (
    can_view_app,
    can_view_track,
    get_user_accessible_tracks,
)
from app.services.relative_date_filters import resolve_relative_date
from app.services.request_scope import matches_workspace


def _view_filter_to_clause(
    field: str, operator: str, value: Any
) -> Optional[Dict[str, Any]]:
    path = _context_field_query_path(field)
    value = resolve_relative_date(value)
    if operator in ("eq", "=="):
        return {path: value}
    if operator in ("neq", "!="):
        return {path: {"$ne": value}}
    if operator == "contains":
        if isinstance(value, str):
            return {path: {"$regex": value, "$options": "i"}}
        return {path: value}
    if operator == "gt":
        return {path: {"$gt": value}}
    if operator == "lt":
        return {path: {"$lt": value}}
    if operator == "gte":
        return {path: {"$gte": value}}
    if operator == "lte":
        return {path: {"$lte": value}}
    if operator == "exists":
        return {path: {"$exists": True}}
    return None


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
    from app.api.entries import _slugify_entry_type_key
    from app.models.edges import CONTAINS
    from app.services.app_graph import ensure_track_attached_operational_model
    from app.services.operational_model_runtime import (
        backfill_view_entry_type_constraints_from_manifest,
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

    allowed_keys = {k for k in (_slugify_entry_type_key(x) for x in keys) if k}
    if not allowed_keys:
        return set()

    cp = await ensure_track_attached_operational_model(track)
    if cp is None:
        return set()

    ets = await cp.nodes(edge=[CONTAINS], node=["EntryType"])
    return {
        et.id
        for et in ets
        if getattr(et, "id", None)
        and _slugify_entry_type_key(getattr(et, "name", "")) in allowed_keys
    }


async def _build_scope_query(
    user_id: str,
    *,
    track_id: Optional[str],
    app_id: Optional[str],
    workspace_id: Optional[str],
) -> Tuple[Dict[str, Any], Set[str], bool]:
    """Return (base_query, verified_track_ids, needs_post_filter)."""
    if track_id and app_id:
        app_id = None

    if app_id:
        if not await can_view_app(user_id, app_id):
            return ({"id": {"$in": []}}, set(), False)
        sp = await App.get(app_id)
        if not sp:
            return ({"id": {"$in": []}}, set(), False)
        if workspace_id and getattr(sp, "workspace_id", "") != workspace_id:
            return ({"id": {"$in": []}}, set(), False)
        tracks = await sp.nodes(edge=["CONTAINS"], node=["Track"])
        visibility = await asyncio.gather(
            *(can_view_track(user_id, tr.id) for tr in tracks)
        )
        visible_track_ids = [tr.id for tr, ok in zip(tracks, visibility) if ok]
        if not visible_track_ids:
            return ({"id": {"$in": []}}, set(), False)
        return (
            {"context.track_id": {"$in": visible_track_ids}},
            set(visible_track_ids),
            False,
        )

    if track_id:
        if not await can_view_track(user_id, track_id):
            return ({"id": {"$in": []}}, set(), False)
        track = await Track.get(track_id)
        if workspace_id and (
            track is None or getattr(track, "workspace_id", "") != workspace_id
        ):
            return ({"id": {"$in": []}}, set(), False)
        return ({"context.track_id": track_id}, {track_id}, False)

    tracks = await get_user_accessible_tracks(user_id)
    if workspace_id:
        tracks = [t for t in tracks if matches_workspace(t, workspace_id)]
    track_ids = [t.id for t in tracks]
    verified = set(track_ids)
    if not track_ids:
        if workspace_id is None:
            return (
                {"context.author_id": user_id},
                verified,
                True,
            )
        return ({"id": {"$in": []}}, verified, False)

    if workspace_id is None:
        return (
            {
                "$or": [
                    {"context.track_id": {"$in": track_ids}},
                    {"context.author_id": user_id},
                ]
            },
            verified,
            True,
        )
    return ({"context.track_id": {"$in": track_ids}}, verified, True)


async def _filter_entry_page(
    user_id: str,
    entries: List[Entry],
    verified_track_ids: Set[str],
    *,
    needs_post_filter: bool,
) -> List[Entry]:
    if not needs_post_filter:
        return entries
    out: List[Entry] = []
    pending_track_checks: List[Tuple[Entry, str]] = []
    for entry in entries:
        t_id = (entry.track_id or "").strip()
        if t_id and t_id in verified_track_ids:
            out.append(entry)
            continue
        if t_id:
            pending_track_checks.append((entry, t_id))
    if pending_track_checks:
        checks = await asyncio.gather(
            *(can_view_track(user_id, t_id) for _, t_id in pending_track_checks)
        )
        for (entry, _), ok in zip(pending_track_checks, checks):
            if ok:
                out.append(entry)
    return out


async def fetch_accessible_entries_page(
    user_id: str,
    *,
    track_id: Optional[str] = None,
    app_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    view_node: Any = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = 20,
    include_total: bool = False,
) -> Tuple[List[Entry], dict]:
    """Fetch one page of entries via DB keyset pagination."""
    scope_query, verified_tracks, needs_post_filter = await _build_scope_query(
        user_id,
        track_id=track_id,
        app_id=app_id,
        workspace_id=workspace_id,
    )
    if scope_query.get("id") == {"$in": []}:
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
            for filt in cfg.get("filters") or []:
                field = filt.get("field", "")
                operator = filt.get("operator", "eq")
                value = filt.get("value")
                if not field:
                    continue
                clause = _view_filter_to_clause(field, operator, value)
                if clause:
                    clauses.append(clause)

    search_clause = entry_search_query_clause(q)
    if search_clause:
        clauses.append(search_clause)

    query = _merge_query_clauses(*clauses)
    sort = list(DEFAULT_ENTITY_SORT)
    if view_node is not None:
        cfg = getattr(view_node, "config", None) or {}
        if isinstance(cfg, dict) and cfg.get("sort"):
            sort = _view_sort_to_db_sort(cfg.get("sort") or [])

    page, response = await paginate_entity_find(
        Entry,
        query,
        cursor,
        limit,
        sort=sort,
        include_total=include_total,
    )
    page = await _filter_entry_page(
        user_id, page, verified_tracks, needs_post_filter=needs_post_filter
    )
    return page, response
