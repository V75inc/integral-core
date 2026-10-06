"""Insight helpers for the embedded agent — query, digest, count.

The shape mirrors ``app/services/operational_model_authoring.py``: pure async
functions called by the in-process bridge action and (eventually,
Phase 0c) by the external MCP tool surface. No staging logic in here
— these are reads; the only ``save_view`` write delegates to
``operational_model_authoring.modify_operational_model(action="add_view", ...)`` so view
creation has one canonical implementation.

Time filters accept ISO-8601 date strings (``"2026-05-01"``) or
ISO-8601 datetimes (``"2026-05-01T12:00:00Z"``). String comparison
is sufficient because Entry timestamps are persisted as ISO
strings and ISO is lexicographically orderable. ``period`` shortcuts
("today", "week", "month") are translated to ISO ranges inside the
helper — keeps the agent from having to do date math itself.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

from app.services.notification_paths import entry_path, resolve_resource_action_url

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


_PERIOD_DAYS = {"today": 1, "week": 7, "month": 30, "quarter": 90, "year": 365}


def _period_to_since(period: str, *, now: Optional[datetime] = None) -> str:
    """Translate a period shortcut to an ISO ``since`` timestamp.

    "today" returns midnight UTC of the current day; the others count
    back N days from now. Unknown values default to "today".
    """
    now = now or datetime.now(timezone.utc)
    if period == "today":
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight.isoformat()
    days = _PERIOD_DAYS.get(period, 1)
    return (now - timedelta(days=days)).isoformat()


def _within_window(
    ts: Optional[str], since: Optional[str], until: Optional[str]
) -> bool:
    """ISO string comparison against an optional ``since``/``until`` window.

    Works because ISO is lexicographically orderable. Missing timestamps
    fail the filter (defensive).
    """
    if since and (not ts or ts < since):
        return False
    if until and (not ts or ts > until):
        return False
    return True


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


SortBy = Literal["updated_at", "created_at", "title"]
SortDir = Literal["asc", "desc"]


def _custom_sort_key(sort_by: str) -> Optional[str]:
    """Return the business-field key when sort_by is custom_fields.<key>."""
    prefix = "custom_fields."
    if not sort_by.startswith(prefix):
        return None
    key = sort_by[len(prefix) :]
    if not key or "." in key:
        return None
    return key


def _sort_entries(entries: List[Any], sort_by: str, sort_dir: str) -> None:
    """Order entries in place. Custom fields use the QuerySpec type ranks."""
    reverse = sort_dir == "desc"
    if sort_by == "title":
        entries.sort(
            key=lambda entry: (getattr(entry, "title", "") or "").casefold(),
            reverse=reverse,
        )
        return
    if sort_by == "created_at":
        entries.sort(
            key=lambda entry: getattr(entry, "created_at", "") or "",
            reverse=reverse,
        )
        return
    if sort_by == "updated_at":
        entries.sort(
            key=lambda entry: getattr(entry, "updated_at", "")
            or getattr(entry, "created_at", "")
            or "",
            reverse=reverse,
        )
        return
    field = _custom_sort_key(sort_by)
    if field is None:
        raise ValueError(
            "sort_by must be updated_at, created_at, title, or custom_fields.<key>"
        )
    from app.agentive.services.query_spec import typed_sort_key

    def raw(entry: Any) -> Any:
        fields = getattr(entry, "custom_fields", None) or {}
        if not isinstance(fields, dict):
            return None
        return fields.get(field)

    entries.sort(key=lambda entry: str(getattr(entry, "id", "")))
    present = [entry for entry in entries if raw(entry) is not None]
    missing = [entry for entry in entries if raw(entry) is None]
    present.sort(key=lambda entry: typed_sort_key(raw(entry)), reverse=reverse)
    entries[:] = present + missing


def _entry_visible_status(entry: Any) -> str:
    """Return the status an operational-model user sees for an entry.

    ``Entry.status`` is Integral's platform lifecycle marker (normally
    ``"active"``).  Operational models commonly define their own ``status``
    select field, such as ``New`` or ``In Progress``.  The track UI renders
    that custom field, so agent queries must filter and report the same value
    rather than silently treating every materialized record as ``active``.
    """
    custom_status = (getattr(entry, "custom_fields", {}) or {}).get("status")
    if isinstance(custom_status, str) and custom_status.strip():
        return custom_status
    return str(getattr(entry, "status", "") or "")


def _entry_persistence_candidate_query(filters: Any) -> Optional[Dict[str, Any]]:
    """Compile only exact, stable scalar predicates for database pushdown.

    The full filter contract still runs after authorization. This compiler is
    deliberately a subset: custom-field aliases, relative-date objects,
    arrays, and predicates whose storage semantics are not proven identical
    remain in the canonical in-process evaluator. Applying these predicates
    at read time cannot remove a row that the existing evaluator would accept.
    """
    from app.services.query_filters import normalize_filter_expressions

    clauses: List[Dict[str, Any]] = []
    scalar_fields = {
        "id",
        "title",
        "track_id",
        "type_id",
        "status",
        "created_at",
        "updated_at",
    }
    for expression in normalize_filter_expressions(filters):
        if expression.field not in scalar_fields:
            continue
        path = "id" if expression.field == "id" else f"context.{expression.field}"
        value = expression.value
        if expression.op == "eq" and (value is None or isinstance(value, str)):
            clauses.append({path: value})
        elif expression.op == "neq" and (value is None or isinstance(value, str)):
            clauses.append({path: {"$ne": value}})
        elif expression.op in ("in", "not_in") and isinstance(value, list):
            if all(isinstance(item, str) for item in value):
                op = "$in" if expression.op == "in" else "$nin"
                clauses.append({path: {op: value}})
        elif expression.op in ("gt", "gte", "lt", "lte") and isinstance(value, str):
            clauses.append({path: {f"${expression.op}": value}})
        elif expression.op == "exists" and isinstance(value, bool):
            clauses.append({path: {"$exists": value}})
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


async def query_all_entries(
    *,
    page_size: int = 500,
    **query_kwargs: Any,
) -> Dict[str, Any]:
    """Return every row from the exact query contract without a hidden cap.

    Callers that need to aggregate or apply a declared profile-field predicate
    must not interpret an arbitrary first page as the complete data set. The
    underlying query owns filtering, ordering, scope and its exact total; this
    helper only walks that stable offset pagination until the reported total is
    exhausted.
    """
    if page_size < 1:
        raise ValueError("page_size must be positive")
    offset = 0
    rows: List[Dict[str, Any]] = []
    first: Optional[Dict[str, Any]] = None
    while True:
        page = await query_entries(
            **query_kwargs,
            limit=page_size,
            offset=offset,
        )
        if page.get("error"):
            return page
        if first is None:
            first = page
        page_rows = list(page.get("entries") or [])
        rows.extend(page_rows)
        total = int(page.get("total") or 0)
        offset += len(page_rows)
        if not page_rows or offset >= total:
            break
    result = dict(first or {})
    result["entries"] = rows
    result["total"] = int((first or {}).get("total") or 0)
    result["limit"] = page_size
    result["offset"] = 0
    result["complete"] = len(rows) == result["total"]
    return result


def _entry_type_name_matches(et_name: str, et_filter: str) -> bool:
    """Case-insensitive name match, tolerant of a trailing plural ``s``."""
    name_fold = et_filter.casefold().rstrip("s")
    folded = (et_name or "").casefold().rstrip("s")
    return bool(folded) and (folded == name_fold or name_fold in folded)


def _entry_type_key(entry_type: Any) -> str:
    """Manifest key, or the display name folded the same way."""
    from app.services.operational_model_runtime import slug_manifest_key

    form_schema = getattr(entry_type, "form_schema", None) or {}
    manifest_key = slug_manifest_key(
        str(form_schema.get("_manifest_entry_type_key") or "")
    )
    return manifest_key or slug_manifest_key(str(getattr(entry_type, "name", "") or ""))


def _entry_type_matches_filter(entry_type: Any, et_filter: str) -> bool:
    """True when ``et_filter`` is this type's name or its schema key."""
    from app.services.operational_model_runtime import slug_manifest_key

    if _entry_type_name_matches(str(getattr(entry_type, "name", "") or ""), et_filter):
        return True
    wanted = slug_manifest_key(et_filter)
    if not wanted:
        return False
    if wanted == _entry_type_key(entry_type):
        return True
    return wanted == slug_manifest_key(str(getattr(entry_type, "name", "") or ""))


def _declared_field_keys(entry_type: Any) -> set:
    form_schema = getattr(entry_type, "form_schema", None) or {}
    fields = form_schema.get("fields") or []
    keys = set()
    if not isinstance(fields, list):
        return keys
    for field in fields:
        if not isinstance(field, dict):
            continue
        key = str(field.get("key") or "").strip()
        if key:
            keys.add(key)
    return keys


def _custom_field_filter_keys(normalized_filters: List[Any]) -> List[str]:
    keys: List[str] = []
    seen = set()
    for expr in normalized_filters:
        field = str(getattr(expr, "field", "") or "")
        if not field.startswith("custom_fields."):
            continue
        key = field.split(".", 2)[1]
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


async def _load_entry_types(entries: List[Any]) -> Dict[str, Any]:
    from app.models.nodes import EntryType

    loaded: Dict[str, Any] = {}
    for entry in entries:
        type_id = str(getattr(entry, "type_id", "") or "")
        if not type_id or type_id in loaded:
            continue
        try:
            entry_type = await EntryType.get(type_id)
        except Exception:  # noqa: BLE001 — a missing type is not an unknown field
            entry_type = None
        if entry_type is not None:
            loaded[type_id] = entry_type
    return loaded


def _unknown_custom_filter_fields(
    types_by_id: Dict[str, Any], filter_keys: List[str]
) -> Optional[Dict[str, Any]]:
    """Fields named in a filter that no loaded entry type declares.

    A declared field that simply has no matching row stays a normal empty
    result. When no type could be loaded, stay quiet — absence of schema is
    not evidence the field is unknown.
    """
    if not filter_keys or not types_by_id:
        return None
    declared = set()
    declared_fold = set()
    type_keys = []
    seen_type_keys = set()
    for entry_type in types_by_id.values():
        for key in _declared_field_keys(entry_type):
            declared.add(key)
            declared_fold.add(key.casefold())
        type_key = _entry_type_key(entry_type)
        if type_key and type_key not in seen_type_keys:
            seen_type_keys.add(type_key)
            type_keys.append(type_key)
    unknown = [
        key
        for key in filter_keys
        if key not in declared and key.casefold() not in declared_fold
    ]
    if not unknown:
        return None
    return {"fields": unknown, "entry_type_keys": type_keys}


async def query_entries(
    *,
    user_id: str,
    track_id: Optional[str] = None,
    query: Optional[str] = None,
    status: Optional[str] = None,
    statuses: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    entry_type: Optional[str] = None,
    filters: Optional[Any] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    sort_by: str = "updated_at",
    sort_dir: SortDir = "desc",
    limit: int = 20,
    offset: int = 0,
    workspace_id: Optional[str] = None,
    result_set_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Filtered cross-track or track-scoped entry query.

    Pushes exact stable-scalar predicates into the permission-aware persistence
    read, then applies the full canonical filter pipeline in-process. Other
    predicates remain in-process until their storage semantics can be proven
    equivalent.
    """
    from app.services.permissions import (
        get_user_accessible_entries,
        get_user_accessible_tracks,
    )

    # Resolve track_id. The agent is supposed to pass an Object id
    # like ``n.Track.f51c315429934ad9b6eec9f7`` from a prior
    # ``list_tracks`` call. In practice the agent often shortcuts
    # and passes the track NAME (e.g. ``"Opportunities"``) — which
    # the strict-string match below turns into zero results,
    # producing the "feel free to share opportunities" empty-handed
    # punt the persona prompt prohibits. Resolve transparently: if
    # the value isn't an Object id, look it up by name first.
    scope = None
    if result_set_id:
        from app.services.result_set_scope import open_result_set

        scope = await open_result_set(
            result_set_id,
            principal_id=user_id,
            workspace_id=workspace_id or "",
            query_class="entry",
        )
        if scope.get("error"):
            return {
                "error": scope["error"],
                "detail": scope["detail"],
                "entries": None,
                "total": None,
                "complete": False,
                "limit": limit,
                "offset": offset,
            }

    accessible_tracks = await get_user_accessible_tracks(user_id)
    # B-AGENT-03 workspace gate — narrow the accessible track universe
    # to the active workspace BEFORE name/id resolution + entry fetch.
    if workspace_id:
        accessible_tracks = [
            t
            for t in accessible_tracks
            if getattr(t, "workspace_id", "") == workspace_id
        ]
    if track_id and not track_id.startswith("n.Track."):
        # Case-insensitive name match against accessible tracks.
        # Picks the first exact-name hit; falls back to a substring
        # hit so "opportunities" matches "CRM · Opportunities".
        # If nothing matches, leave track_id unchanged — the result
        # will be empty and ``filters_applied`` will show the
        # caller what we searched for.
        hint_fold = track_id.strip().casefold()
        exact = next(
            (t for t in accessible_tracks if (t.title or "").casefold() == hint_fold),
            None,
        )
        substr = exact or next(
            (t for t in accessible_tracks if hint_fold in (t.title or "").casefold()),
            None,
        )
        if substr:
            track_id = substr.id

    from app.services.query_boundary import generic_entry_read

    excluded_tracks = 0
    open_tracks = []
    named_block = None
    if track_id:
        from app.models.nodes import Track

        named = next((t for t in accessible_tracks if t.id == track_id), None)
        if named is None and track_id.startswith("n.Track."):
            named = await Track.get(track_id)
        if named is not None:
            decision = await generic_entry_read(named)
            if not decision.allowed:
                named_block = decision
    else:
        for track in accessible_tracks:
            if (await generic_entry_read(track)).allowed:
                open_tracks.append(track)
            else:
                excluded_tracks += 1
    if named_block is not None:
        return {
            "entries": [],
            "total": None,
            "records_read": False,
            "limit": limit,
            "offset": offset,
            "refused": named_block.public(track_id=track_id),
            "filters_applied": {
                "track_id": track_id,
                "workspace_id": workspace_id,
                "status": None,
                "tags": None,
                "entry_type": None,
                "filters": filters or None,
                "since": since,
                "until": until,
                "query": query or None,
            },
        }

    # When an explicit Track id is supplied, reject it if it's outside
    # the active workspace gate. Don't try to resolve cross-workspace.
    if workspace_id and track_id and track_id.startswith("n.Track."):
        if not any(t.id == track_id for t in accessible_tracks):
            return {
                "entries": [],
                "total": 0,
                "limit": limit,
                "offset": offset,
                "filters_applied": {
                    "track_id": track_id,
                    "workspace_id": workspace_id,
                    "status": None,
                    "tags": None,
                    "entry_type": None,
                    "filters": filters or None,
                    "since": since,
                    "until": until,
                    "query": query or None,
                },
            }

    # Push down only exact scalar predicates whose JSON persistence semantics
    # match the canonical evaluator. The complete evaluator below remains the
    # authority for the returned rows.
    from app.services.query_filters import normalize_filter_expressions

    normalized_filters = normalize_filter_expressions(filters)
    candidate_query = _entry_persistence_candidate_query(normalized_filters)

    # Plain reads and exact scalar filters use database keyset pages. Complex
    # predicates continue through the canonical in-memory evaluator below.
    if (
        result_set_id is None
        and not query
        and not status
        and not statuses
        and not tags
        and not entry_type
        and not since
        and not until
        and not normalized_filters
        and all(
            _entry_persistence_candidate_query([expression]) is not None
            for expression in normalized_filters
        )
        and sort_by in ("updated_at", "created_at", "title")
    ):
        from app.models.nodes import Track as TrackNode

        selected_tracks = (
            [track for track in accessible_tracks if track.id == track_id]
            if track_id
            else open_tracks
        )
        if all(isinstance(track, TrackNode) for track in selected_tracks):
            from app.services.permissions import get_user_accessible_entry_page

            take = max(1, offset + limit)
            sort_field = {
                "updated_at": "context.updated_at",
                "created_at": "context.created_at",
                "title": "context.title",
            }[sort_by]
            direction = -1 if sort_dir == "desc" else 1
            entries: List[Any] = []
            total = 0
            for track in selected_tracks:
                partitions = (
                    [
                        (sort_field, {"context.updated_at": {"$gt": ""}}),
                        (
                            "context.created_at",
                            {
                                "$or": [
                                    {"context.updated_at": {"$exists": False}},
                                    {"context.updated_at": None},
                                    {"context.updated_at": ""},
                                ]
                            },
                        ),
                    ]
                    if sort_by == "updated_at"
                    else [(sort_field, {})]
                )
                for page_sort, partition in partitions:
                    parts = [part for part in (candidate_query, partition) if part]
                    page_query = parts[0] if len(parts) == 1 else {"$and": parts}
                    page = await get_user_accessible_entry_page(
                        user_id,
                        track.id,
                        limit=take,
                        sort=[(page_sort, direction)],
                        candidate_query=page_query,
                        workspace_id=workspace_id,
                    )
                    entries.extend(page["entries"])
                    total += int(page["total"])
            _sort_entries(entries, sort_by, sort_dir)
            selected = entries[offset : offset + limit]
            result = {
                "entries": [
                    {
                        "id": entry.id,
                        "title": getattr(entry, "title", ""),
                        "track_id": getattr(entry, "track_id", ""),
                        "status": _entry_visible_status(entry),
                        "tags": getattr(entry, "tags", []) or [],
                        "type_id": getattr(entry, "type_id", ""),
                        "custom_fields": dict(
                            getattr(entry, "custom_fields", {}) or {}
                        ),
                        "updated_at": getattr(entry, "updated_at", None),
                        "created_at": getattr(entry, "created_at", None),
                        "action_url": (
                            entry_path(entry.id, getattr(entry, "track_id", "") or "")
                            if getattr(entry, "track_id", "")
                            else None
                        ),
                    }
                    for entry in selected
                ],
                "total": total,
                "limit": limit,
                "offset": offset,
                "filters_applied": {
                    "track_id": track_id,
                    "workspace_id": workspace_id,
                    "status": None,
                    "tags": None,
                    "entry_type": None,
                    "filters": filters or None,
                    "since": None,
                    "until": None,
                    "query": None,
                    "sort_by": sort_by,
                    "sort_dir": sort_dir,
                    "result_set_id": None,
                },
            }
            if not track_id and excluded_tracks:
                result["boundary"] = {
                    "excluded_tracks": excluded_tracks,
                    "declared_query_required": True,
                }
            return result

    # Dynamic fields, status/tag/type filters, keyword search, and date windows
    # are evaluated in bounded batches. Keep only the requested sorted window
    # in memory while still counting every visible match exactly.
    complex_read = bool(
        query
        or status
        or statuses
        or tags
        or entry_type
        or since
        or until
        or any(
            _entry_persistence_candidate_query([expression]) is None
            for expression in normalized_filters
        )
    )
    selected_tracks = (
        [track for track in accessible_tracks if track.id == track_id]
        if track_id
        else open_tracks
    )
    from app.models.nodes import Track as TrackNode

    if (
        complex_read
        and result_set_id is None
        and all(isinstance(track, TrackNode) for track in selected_tracks)
    ):
        from app.services.permissions import iter_user_accessible_entry_batches
        from app.services.query_filters import entry_matches_filters
        from app.services.retrieval.keyword_match import matches_keywords, tokenize

        status_set = {s for s in (statuses or []) if s}
        if status:
            status_set.add(status)
        tag_set = set(tags or [])
        query_terms = tokenize(query or "")
        max_window = max(1, offset + limit)
        selected_entries: List[Any] = []
        total = 0
        for track in selected_tracks:
            async for batch in iter_user_accessible_entry_batches(
                user_id,
                track.id,
                candidate_query=candidate_query,
                workspace_id=workspace_id,
            ):
                batch_accept_type_ids: Optional[set] = None
                if entry_type:
                    candidate_type_ids = {
                        getattr(entry, "type_id", "")
                        for entry in batch
                        if getattr(entry, "type_id", "")
                    }
                    if entry_type in candidate_type_ids:
                        batch_accept_type_ids = {entry_type}
                    else:
                        from app.models.nodes import EntryType

                        wanted = entry_type.strip().casefold().rstrip("s")
                        batch_accept_type_ids = set()
                        for type_id in candidate_type_ids:
                            try:
                                entry_type_node = await EntryType.get(type_id)
                            except Exception:
                                entry_type_node = None
                            name = (
                                (getattr(entry_type_node, "name", "") or "")
                                .casefold()
                                .rstrip("s")
                            )
                            if name and (name == wanted or wanted in name):
                                batch_accept_type_ids.add(type_id)
                for entry in batch:
                    if status_set and _entry_visible_status(entry) not in status_set:
                        continue
                    if tag_set and not (
                        tag_set & set(getattr(entry, "tags", []) or [])
                    ):
                        continue
                    if (
                        batch_accept_type_ids is not None
                        and getattr(entry, "type_id", "") not in batch_accept_type_ids
                    ):
                        continue
                    if normalized_filters and not entry_matches_filters(
                        entry, normalized_filters
                    ):
                        continue
                    if not _within_window(
                        getattr(entry, "updated_at", None)
                        or getattr(entry, "created_at", None),
                        since,
                        until,
                    ):
                        continue
                    if query_terms:
                        haystack = (
                            (getattr(entry, "title", "") or "")
                            + " "
                            + (getattr(entry, "body", "") or "")
                        )
                        if not matches_keywords(haystack, query_terms, require="any"):
                            continue
                    total += 1
                    selected_entries.append(entry)
                _sort_entries(selected_entries, sort_by, sort_dir)
                del selected_entries[max_window:]

        sliced = selected_entries[offset : offset + limit]
        result = {
            "entries": [
                {
                    "id": entry.id,
                    "title": getattr(entry, "title", ""),
                    "track_id": getattr(entry, "track_id", ""),
                    "status": _entry_visible_status(entry),
                    "tags": getattr(entry, "tags", []) or [],
                    "type_id": getattr(entry, "type_id", ""),
                    "custom_fields": dict(getattr(entry, "custom_fields", {}) or {}),
                    "updated_at": getattr(entry, "updated_at", None),
                    "created_at": getattr(entry, "created_at", None),
                    "action_url": (
                        entry_path(entry.id, getattr(entry, "track_id", "") or "")
                        if getattr(entry, "track_id", "")
                        else None
                    ),
                }
                for entry in sliced
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
            "filters_applied": {
                "track_id": track_id,
                "workspace_id": workspace_id,
                "status": sorted(status_set) or None,
                "tags": sorted(tag_set) or None,
                "entry_type": entry_type,
                "filters": filters or None,
                "since": since,
                "until": until,
                "query": query or None,
                "sort_by": sort_by,
                "sort_dir": sort_dir,
                "result_set_id": None,
            },
        }
        if not track_id and excluded_tracks:
            result["boundary"] = {
                "excluded_tracks": excluded_tracks,
                "declared_query_required": True,
            }
        return result

    # Gather candidate entries. Packaged and inactive Apps are already removed.
    if track_id:
        entries = await get_user_accessible_entries(
            user_id,
            track_id,
            strict=True,
            candidate_query=candidate_query,
        )
    else:
        entries = []
        for t in open_tracks:
            entries.extend(
                await get_user_accessible_entries(
                    user_id,
                    t.id,
                    strict=True,
                    candidate_query=candidate_query,
                )
            )

    scope_meta = None
    if scope is not None:
        from app.models.nodes import Entry
        from app.services.result_set_scope import classify_missing, schema_drift

        if schema_drift(scope["member_schema"], entries):
            return {
                "error": "schema_drift",
                "detail": "a member schema changed; no values returned",
                "entries": None,
                "total": None,
                "complete": False,
                "limit": limit,
                "offset": offset,
            }
        allowed = set(scope["member_ids"])
        present = {str(getattr(item, "id", "")) for item in entries}
        missing = [item_id for item_id in scope["member_ids"] if item_id not in present]
        classified = await classify_missing(missing, Entry.get)
        entries = [item for item in entries if str(getattr(item, "id", "")) in allowed]
        scope_meta = {
            "result_set_id": scope["result_set_id"],
            "membership_at": scope["membership_at"],
            "value_read_at": datetime.now(timezone.utc).isoformat(),
            "absent_ids": classified["absent_ids"],
            "excluded_ids": classified["excluded_ids"],
        }

    # Filter pipeline.
    from app.services.query_filters import entry_matches_filters
    from app.services.retrieval.keyword_match import matches_keywords, tokenize

    # Keep the resident's compact entry query on the declared field contract
    # used by saved views and governed queries. Normalize before filtering so an
    # invalid field/operator is rejected even when there are no candidate rows.
    status_set = {s for s in (statuses or []) if s}
    if status:
        status_set.add(status)
    tag_set = set(tags or [])
    # Keyword-term match (mode-adaptive): tokenize the query into significant
    # terms (stopwords / boolean noise / short tokens dropped). No terms →
    # no text filter (legacy "empty query = all entries" behaviour). With
    # terms, keep entries where ANY term hits the title+body haystack — so a
    # multi-word query like "Acme Inc company" or an "A OR B OR C" blob finds
    # entries by term overlap rather than a literal full-string substring.
    query_terms = tokenize(query or "")

    # Resolve entry_type filter to a set of acceptable type_ids.
    #
    # Earlier this filter only compared against ``e.type_id``, which
    # forced the agent to pass opaque ids like
    # ``n.EntryType.91428e0c`` (it never does — it passes natural
    # names like "opportunity"). Net effect: every entry-type filter
    # returned zero results, and the agent's empty-handed reply
    # produced the "feel free to share" punt the persona prompt
    # forbids.
    #
    # Accept an exact type id, a display name (case- and
    # singular/plural-insensitive), or the manifest key. ``pay_calendar``
    # matches the type named "Pay Calendar" because spaces, underscores,
    # and hyphens fold to the same slug. Types load once per call.
    filter_field_keys = _custom_field_filter_keys(normalized_filters)
    types_by_id = (
        await _load_entry_types(entries)
        if (entry_type and entry_type.strip()) or filter_field_keys
        else {}
    )
    accept_type_ids: Optional[set] = None
    if entry_type:
        et_filter = entry_type.strip()
        if et_filter:
            candidate_type_ids = {
                getattr(e, "type_id", "") for e in entries if getattr(e, "type_id", "")
            }
            if et_filter in candidate_type_ids:
                accept_type_ids = {et_filter}
            else:
                accept_type_ids = {
                    tid
                    for tid, et in types_by_id.items()
                    if _entry_type_matches_filter(et, et_filter)
                }
    unknown_filters = _unknown_custom_filter_fields(types_by_id, filter_field_keys)

    filtered: List[Any] = []
    for e in entries:
        if status_set and _entry_visible_status(e) not in status_set:
            continue
        if tag_set:
            entry_tags = set(getattr(e, "tags", []) or [])
            if not (tag_set & entry_tags):
                continue
        if (
            accept_type_ids is not None
            and getattr(e, "type_id", "") not in accept_type_ids
        ):
            continue
        if normalized_filters and not entry_matches_filters(e, normalized_filters):
            continue
        if not _within_window(
            getattr(e, "updated_at", None) or getattr(e, "created_at", None),
            since,
            until,
        ):
            continue
        if query_terms:
            haystack = (
                (getattr(e, "title", "") or "") + " " + (getattr(e, "body", "") or "")
            )
            if not matches_keywords(haystack, query_terms, require="any"):
                continue
        filtered.append(e)

    _sort_entries(filtered, sort_by, sort_dir)

    total = len(filtered)
    sliced = filtered[offset : offset + limit]

    result = {
        "entries": [
            {
                "id": e.id,
                "title": getattr(e, "title", ""),
                "track_id": getattr(e, "track_id", ""),
                "status": _entry_visible_status(e),
                "tags": getattr(e, "tags", []) or [],
                "type_id": getattr(e, "type_id", ""),
                # Dashboard and resident query consumers must be able to reason
                # about the typed schema values they are displaying. Omitting this
                # made every consumer silently fall back to platform ``status``.
                "custom_fields": dict(getattr(e, "custom_fields", {}) or {}),
                "updated_at": getattr(e, "updated_at", None),
                "created_at": getattr(e, "created_at", None),
                "action_url": (
                    entry_path(e.id, getattr(e, "track_id", "") or "")
                    if getattr(e, "track_id", "")
                    else None
                ),
            }
            for e in sliced
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
        "filters_applied": {
            "track_id": track_id,
            "workspace_id": workspace_id,
            "status": sorted(status_set) or None,
            "tags": sorted(tag_set) or None,
            "entry_type": entry_type,
            "filters": filters or None,
            "since": since,
            "until": until,
            "query": query or None,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
            "result_set_id": result_set_id,
        },
        **(
            {"unknown_filter_fields": unknown_filters}
            if unknown_filters is not None
            else {}
        ),
    }
    if scope_meta is not None:
        result["scope"] = scope_meta
    if not track_id and excluded_tracks:
        result["boundary"] = {
            "excluded_tracks": excluded_tracks,
            "declared_query_required": True,
        }
    return result


# ---------------------------------------------------------------------------
# Digest
# ---------------------------------------------------------------------------


async def activity_digest(
    *,
    user_id: str,
    scope: Literal["user", "app", "track"] = "user",
    scope_id: Optional[str] = None,
    period: Literal["today", "week", "month"] = "today",
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Activity summary scoped to user, app_node, or track.

    Includes total tracks, total entries, and per-track summaries
    (entry count + recent-touched entries within the period).

    When ``workspace_id`` is set, the result is gated to tracks that
    live in that workspace BEFORE the per-user access filter runs
    (B-AGENT-03 two-layer scope contract). When None, falls back to
    cross-workspace behaviour for legacy callers.
    """
    from app.services.permissions import (
        get_user_accessible_entries,
        get_user_accessible_tracks,
    )

    since = _period_to_since(period)
    tracks = await get_user_accessible_tracks(user_id)

    # B-AGENT-03: workspace gate (first layer). Filter the user-accessible
    # set down to the active workspace before any other scope-based
    # narrowing. ``Track.workspace_id`` is persisted on every Track
    # (backend/app/models/nodes.py:409).
    if workspace_id:
        tracks = [t for t in tracks if getattr(t, "workspace_id", "") == workspace_id]

    # Scope filter — ``scope_id`` narrows to one app's tracks or one track.
    # Same name-vs-id tolerance as ``query_entries``: the agent often
    # passes a track NAME rather than the Object id.
    if scope == "track" and scope_id:
        if scope_id.startswith("n.Track."):
            tracks = [t for t in tracks if t.id == scope_id]
        else:
            hint_fold = scope_id.strip().casefold()
            exact = [t for t in tracks if (t.title or "").casefold() == hint_fold]
            substr = exact or [
                t for t in tracks if hint_fold in (t.title or "").casefold()
            ]
            tracks = substr[:1] if substr else []
    elif scope == "app" and scope_id:
        # B-AGENT-05: scope=app + scope_id narrows to the App's child
        # tracks (App —CONTAINS→ Track). Accepts either an Object id
        # (``n.WorkspaceApp.*``) or a fuzzy name match — same
        # tolerance as the scope=track branch above. Falls back to the
        # workspace-gated set when scope_id can't be resolved so the
        # caller still gets a sensible (non-empty) digest.
        from app.models.nodes import App as _WorkspaceApp

        app_obj = None
        if scope_id.startswith("n.WorkspaceApp."):
            app_obj = await _WorkspaceApp.get(scope_id)
        else:
            # Name match across accessible tracks' parent apps.
            seen_app_ids: set = set()
            for t in tracks:
                pa_id = getattr(t, "app_id", "") or ""
                if pa_id and pa_id not in seen_app_ids:
                    seen_app_ids.add(pa_id)
            hint_fold = scope_id.strip().casefold()
            for pa_id in seen_app_ids:
                pa = await _WorkspaceApp.get(pa_id)
                if pa and (pa.name or "").casefold() == hint_fold:
                    app_obj = pa
                    break
            if app_obj is None:
                for pa_id in seen_app_ids:
                    pa = await _WorkspaceApp.get(pa_id)
                    if pa and hint_fold in (pa.name or "").casefold():
                        app_obj = pa
                        break
        if app_obj is not None:
            child_track_ids: set[str] = set()
            cursor: Optional[str] = None
            while True:
                page, cursor = await app_obj.nodes_page(
                    edge=["CONTAINS"], node=["Track"], cursor=cursor, limit=200
                )
                child_track_ids.update(ct.id for ct in page)
                if not cursor:
                    break
            tracks = [t for t in tracks if t.id in child_track_ids]

    from app.services.query_boundary import generic_entry_read

    open_tracks = []
    blocked = []
    for track in tracks:
        decision = await generic_entry_read(track)
        if decision.allowed:
            open_tracks.append(track)
        else:
            blocked.append(decision)
    tracks = open_tracks

    from app.models.nodes import Track as TrackNode

    track_summaries: List[Dict[str, Any]] = []
    total_entries = 0
    recent_entry_count = 0
    for t in tracks:
        if isinstance(t, TrackNode):
            from app.services.permissions import (
                count_user_accessible_entries,
                get_user_accessible_entry_page,
            )

            entry_count = await count_user_accessible_entries(user_id, t.id)
            recent_queries = [
                {"context.updated_at": {"$gte": since}},
                {
                    "$and": [
                        {
                            "$or": [
                                {"context.updated_at": {"$exists": False}},
                                {"context.updated_at": None},
                                {"context.updated_at": ""},
                            ]
                        },
                        {"context.created_at": {"$gte": since}},
                    ]
                },
            ]
            recent_entries: List[Any] = []
            recent_count = 0
            for index, candidate_query in enumerate(recent_queries):
                page = await get_user_accessible_entry_page(
                    user_id,
                    t.id,
                    limit=3,
                    sort=[
                        (
                            (
                                "context.updated_at"
                                if index == 0
                                else "context.created_at"
                            ),
                            -1,
                        )
                    ],
                    candidate_query=candidate_query,
                    workspace_id=workspace_id,
                )
                recent_count += int(page["total"])
                recent_entries.extend(page["entries"])
            recent = sorted(
                recent_entries,
                key=lambda entry: getattr(entry, "updated_at", None)
                or getattr(entry, "created_at", "")
                or "",
                reverse=True,
            )[:3]
        else:
            # Preserve adapter and unit-test behavior; production Tracks use
            # exact counts and bounded keyset reads above.
            entries = await get_user_accessible_entries(user_id, t.id, strict=True)
            recent = [
                e
                for e in entries
                if _within_window(
                    getattr(e, "updated_at", None) or getattr(e, "created_at", None),
                    since,
                    None,
                )
            ]
            entry_count = len(entries)
            recent_count = len(recent)
        total_entries += entry_count
        recent_entry_count += recent_count
        track_summaries.append(
            {
                "track_id": t.id,
                "title": getattr(t, "title", ""),
                "entry_count": entry_count,
                "recent_count": recent_count,
                "recent_titles": [
                    getattr(e, "title", "")
                    for e in sorted(
                        recent,
                        key=lambda x: getattr(x, "updated_at", "") or "",
                        reverse=True,
                    )[:3]
                ],
                "action_url": resolve_resource_action_url("track", t.id),
            }
        )

    digest = {
        "scope": scope,
        "scope_id": scope_id,
        "workspace_id": workspace_id,
        "period": period,
        "since": since,
        "total_tracks": len(tracks),
        "total_entries": total_entries,
        "recent_entry_count": recent_entry_count,
        "excluded_tracks": len(blocked),
        "track_summaries": track_summaries,
    }
    if blocked and not tracks and scope in ("track", "app"):
        digest["refused"] = blocked[0].public()
    return digest


# ---------------------------------------------------------------------------
# Count (group-by)
# ---------------------------------------------------------------------------


GroupBy = Literal["track", "status", "tag", "entry_type", "date"]


async def _tag_labels(tag_ids: List[str]) -> Dict[str, str]:
    """Map tag node ids to their names. An unknown id keeps no label."""
    from app.models.nodes import Tag

    labels: Dict[str, str] = {}
    for tag_id in tag_ids:
        if not tag_id or tag_id.startswith("("):
            continue
        try:
            tag = await Tag.get(tag_id)
        except Exception:  # noqa: BLE001
            tag = None
        name = (getattr(tag, "name", "") or "").strip() if tag else ""
        if name:
            labels[tag_id] = name
    return labels


async def count_entries_grouped(
    *,
    user_id: str,
    group_by: GroupBy,
    track_id: Optional[str] = None,
    status: Optional[str] = None,
    statuses: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    entry_type: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    filters: Optional[Any] = None,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Count entries grouped by the given dimension.

    Applies the same filter pipeline as ``query_entries``, including
    the B-AGENT-03 workspace gate when ``workspace_id`` is set.
    """
    from app.services.permissions import get_user_accessible_tracks

    # An unfiltered Track grouping needs only one exact count per visible
    # Track. Avoid query_all_entries, which would hydrate the whole corpus.
    if (
        group_by == "track"
        and not track_id
        and not status
        and not statuses
        and not tags
        and not entry_type
        and not since
        and not until
        and not filters
    ):
        from app.models.nodes import Track as TrackNode
        from app.services.permissions import (
            count_user_accessible_entries,
        )
        from app.services.query_boundary import generic_entry_read

        tracks = await get_user_accessible_tracks(user_id)
        if workspace_id:
            tracks = [
                track
                for track in tracks
                if getattr(track, "workspace_id", "") == workspace_id
            ]
        if track_id:
            if not track_id.startswith("n.Track."):
                folded = track_id.strip().casefold()
                exact = next(
                    (t for t in tracks if (t.title or "").casefold() == folded), None
                )
                matched = exact or next(
                    (t for t in tracks if folded in (t.title or "").casefold()), None
                )
                track_id = matched.id if matched else track_id
            tracks = [track for track in tracks if track.id == track_id]
        if tracks and all(isinstance(track, TrackNode) for track in tracks):
            counts: List[tuple[Any, int]] = []
            excluded_tracks = 0
            for track in tracks:
                decision = await generic_entry_read(track)
                if not decision.allowed:
                    excluded_tracks += 1
                    continue
                counts.append(
                    (track, await count_user_accessible_entries(user_id, track.id))
                )
            groups = [
                {
                    "key": track.id,
                    "label": getattr(track, "title", track.id),
                    "count": count,
                }
                for track, count in sorted(
                    counts, key=lambda item: item[1], reverse=True
                )
            ]
            result = {
                "group_by": group_by,
                "total_matched": sum(count for _, count in counts),
                "groups": groups,
                "filters_applied": {
                    "track_id": track_id,
                    "workspace_id": workspace_id,
                    "status": None,
                    "tags": None,
                    "entry_type": None,
                    "filters": None,
                    "since": None,
                    "until": None,
                },
            }
            if not track_id and excluded_tracks:
                result["boundary"] = {
                    "excluded_tracks": excluded_tracks,
                    "declared_query_required": True,
                }
            return result

    # Aggregate filtered grouped counts directly from visible Entry batches.
    # This keeps status/tag/type/custom-field groupings bounded as well.
    from app.models.nodes import Track as TrackNode

    accessible_tracks = await get_user_accessible_tracks(user_id)
    if workspace_id:
        accessible_tracks = [
            track
            for track in accessible_tracks
            if getattr(track, "workspace_id", "") == workspace_id
        ]
    if track_id and not track_id.startswith("n.Track."):
        folded = track_id.strip().casefold()
        exact = next(
            (
                track
                for track in accessible_tracks
                if (track.title or "").casefold() == folded
            ),
            None,
        )
        matched = exact or next(
            (
                track
                for track in accessible_tracks
                if folded in (track.title or "").casefold()
            ),
            None,
        )
        if matched:
            track_id = matched.id
    selected_tracks = (
        [track for track in accessible_tracks if track.id == track_id]
        if track_id
        else accessible_tracks
    )
    if all(isinstance(track, TrackNode) for track in selected_tracks):
        from app.services.permissions import iter_user_accessible_entry_batches
        from app.services.query_boundary import generic_entry_read
        from app.services.query_filters import (
            entry_matches_filters,
            normalize_filter_expressions,
        )

        if track_id and not selected_tracks:
            boundary_query = await query_entries(
                user_id=user_id,
                track_id=track_id,
                workspace_id=workspace_id,
                limit=1,
            )
            if boundary_query.get("refused"):
                return {
                    "group_by": group_by,
                    "total_matched": 0,
                    "groups": [],
                    "refused": boundary_query["refused"],
                    "filters_applied": boundary_query.get("filters_applied", {}),
                }
        if track_id and not selected_tracks and track_id.startswith("n.Track."):
            named_track = await TrackNode.get(track_id)
            if named_track and workspace_id:
                from app.services.request_scope import _effective_workspace_id_of

                if _effective_workspace_id_of(named_track) != workspace_id:
                    named_track = None
            if named_track:
                denied = await generic_entry_read(named_track)
                if not denied.allowed:
                    return {
                        "group_by": group_by,
                        "total_matched": 0,
                        "groups": [],
                        "refused": denied.public(track_id=track_id),
                        "filters_applied": {
                            "track_id": track_id,
                            "workspace_id": workspace_id,
                            "status": status,
                            "tags": tags,
                            "entry_type": entry_type,
                            "filters": filters or None,
                            "since": since,
                            "until": until,
                        },
                    }
        open_tracks = []
        excluded_tracks = 0
        for track in selected_tracks:
            decision = await generic_entry_read(track)
            if decision.allowed:
                open_tracks.append(track)
            else:
                if track_id:
                    return {
                        "group_by": group_by,
                        "total_matched": 0,
                        "groups": [],
                        "refused": decision.public(track_id=track.id),
                        "filters_applied": {
                            "track_id": track_id,
                            "workspace_id": workspace_id,
                            "status": status,
                            "tags": tags,
                            "entry_type": entry_type,
                            "filters": filters or None,
                            "since": since,
                            "until": until,
                        },
                    }
                excluded_tracks += 1
        status_set = {value for value in (statuses or []) if value}
        if status:
            status_set.add(status)
        tag_set = set(tags or [])
        normalized_filters = normalize_filter_expressions(filters)
        candidate_query = _entry_persistence_candidate_query(normalized_filters)
        stream_counter: Counter[str] = Counter()
        total_matched = 0
        type_labels: Dict[str, str] = {}
        track_labels = {
            track.id: getattr(track, "title", track.id) for track in open_tracks
        }
        for track in open_tracks:
            async for batch in iter_user_accessible_entry_batches(
                user_id,
                track.id,
                candidate_query=candidate_query,
                workspace_id=workspace_id,
            ):
                accepted_type_ids: Optional[set] = None
                if entry_type:
                    type_ids = {
                        getattr(entry, "type_id", "")
                        for entry in batch
                        if getattr(entry, "type_id", "")
                    }
                    if entry_type in type_ids or entry_type.startswith("n.EntryType."):
                        accepted_type_ids = {entry_type}
                    else:
                        from app.models.nodes import EntryType

                        wanted = entry_type.strip().casefold().rstrip("s")
                        accepted_type_ids = set()
                        for type_id in type_ids:
                            try:
                                type_node = await EntryType.get(type_id)
                            except Exception:
                                type_node = None
                            name = (
                                (getattr(type_node, "name", "") or "")
                                .casefold()
                                .rstrip("s")
                            )
                            if name and (name == wanted or wanted in name):
                                accepted_type_ids.add(type_id)
                    if accepted_type_ids:
                        from app.models.nodes import EntryType

                        for type_id in accepted_type_ids:
                            type_node = await EntryType.get(type_id)
                            if type_node:
                                type_labels[type_id] = getattr(
                                    type_node, "name", type_id
                                )
                for entry in batch:
                    if status_set and _entry_visible_status(entry) not in status_set:
                        continue
                    if tag_set and not (
                        tag_set & set(getattr(entry, "tags", []) or [])
                    ):
                        continue
                    if (
                        accepted_type_ids is not None
                        and getattr(entry, "type_id", "") not in accepted_type_ids
                    ):
                        continue
                    if normalized_filters and not entry_matches_filters(
                        entry, normalized_filters
                    ):
                        continue
                    if not _within_window(
                        getattr(entry, "updated_at", None)
                        or getattr(entry, "created_at", None),
                        since,
                        until,
                    ):
                        continue
                    total_matched += 1
                    if group_by == "track":
                        stream_counter[track.id] += 1
                    elif group_by == "status":
                        stream_counter[_entry_visible_status(entry) or "(none)"] += 1
                    elif group_by == "tag":
                        for tag_value in getattr(entry, "tags", []) or []:
                            stream_counter[tag_value] += 1
                    elif group_by == "entry_type":
                        type_id = getattr(entry, "type_id", "") or "(none)"
                        stream_counter[type_id] += 1
                    elif group_by == "date":
                        created = getattr(entry, "created_at", None)
                        stream_counter[
                            str(created)[:10] if created else "(unknown)"
                        ] += 1
                    else:
                        return {
                            "error": "invalid_argument",
                            "detail": f"Unknown group_by: {group_by}",
                        }
        if group_by == "date":
            groups = [
                {"key": key, "label": key, "count": count}
                for key, count in sorted(stream_counter.items())
            ]
        else:
            groups = []
            for key, count in stream_counter.most_common():
                label = key
                if group_by == "track":
                    label = track_labels.get(key, key)
                elif group_by == "entry_type":
                    label = type_labels.get(key, key)
                groups.append({"key": key, "label": label, "count": count})
        result = {
            "group_by": group_by,
            "total_matched": total_matched,
            "groups": groups,
            "filters_applied": {
                "track_id": track_id,
                "workspace_id": workspace_id,
                "status": sorted(status_set) or None,
                "tags": sorted(tag_set) or None,
                "entry_type": entry_type,
                "filters": filters or None,
                "since": since,
                "until": until,
            },
        }
        if not track_id and excluded_tracks:
            result["boundary"] = {
                "excluded_tracks": excluded_tracks,
                "declared_query_required": True,
            }
        return result

    # Reuse the query helper to apply filters (with high limit so we
    # see all matches), then aggregate.
    queried = await query_all_entries(
        user_id=user_id,
        track_id=track_id,
        status=status,
        statuses=statuses,
        tags=tags,
        entry_type=entry_type,
        filters=filters,
        since=since,
        until=until,
        workspace_id=workspace_id,
    )
    if queried.get("refused"):
        return {
            "group_by": group_by,
            "total_matched": 0,
            "groups": [],
            "refused": queried["refused"],
            "filters_applied": queried.get("filters_applied", {}),
        }
    entries = queried.get("entries", [])
    result_set_id = ""

    counter: Counter[str] = Counter()
    if group_by == "track":
        # Resolve track titles for prettier output.
        from app.models.nodes import Track

        track_titles: Dict[str, str] = {}
        for e in entries:
            tid = e.get("track_id") or "(unknown)"
            counter[tid] += 1
        # One lookup per distinct track id.
        for tid in list(counter.keys()):
            if tid == "(unknown)":
                continue
            try:
                t = await Track.get(tid)
                if t:
                    track_titles[tid] = getattr(t, "title", tid)
            except Exception:  # noqa: BLE001
                continue
        groups = [
            {"key": tid, "label": track_titles.get(tid, tid), "count": n}
            for tid, n in counter.most_common()
        ]
    elif group_by == "status":
        for e in entries:
            counter[e.get("status") or "(none)"] += 1
        groups = [{"key": s, "label": s, "count": n} for s, n in counter.most_common()]
    elif group_by == "tag":
        for e in entries:
            for tag in e.get("tags", []) or []:
                counter[str(tag)] += 1
        labels = await _tag_labels(list(counter.keys()))
        if track_id:
            from app.models.nodes import Tag

            for tag in await Tag.find({"context.track_id": track_id}):
                tag_id = str(getattr(tag, "id", "") or "")
                if not tag_id:
                    continue
                counter.setdefault(tag_id, 0)
                name = (getattr(tag, "name", "") or "").strip()
                if name:
                    labels[tag_id] = name
        groups = [
            {"key": tag_id, "label": labels.get(tag_id, tag_id), "count": count}
            for tag_id, count in counter.items()
        ]
        groups.sort(key=lambda row: (str(row["label"]).casefold(), row["key"]))
        from app.services.focused_additions import store_tag_result_set

        result_set_id = await store_tag_result_set(
            user_id=user_id,
            workspace_id=workspace_id,
            entries=entries,
            labels=labels,
        )
    elif group_by == "entry_type":
        for e in entries:
            counter[e.get("type_id") or "(none)"] += 1
        groups = [{"key": k, "label": k, "count": n} for k, n in counter.most_common()]
    elif group_by == "date":
        for e in entries:
            raw = (
                e.get("created_at")
                if isinstance(e, dict)
                else getattr(e, "created_at", None)
            )
            bucket = str(raw)[:10] if raw else "(unknown)"
            counter[bucket] += 1
        groups = [
            {"key": d, "label": d, "count": n}
            for d, n in sorted(counter.items(), key=lambda x: x[0])
        ]
    else:
        return {"error": "invalid_argument", "detail": f"Unknown group_by: {group_by}"}

    grouped = {
        "group_by": group_by,
        "total_matched": queried.get("total", 0),
        "groups": groups,
        "filters_applied": queried.get("filters_applied", {}),
    }
    if group_by == "tag" and result_set_id:
        grouped["result_set_id"] = result_set_id
    if group_by == "tag":
        from app.services.turn_binding import tag_count_reply

        grouped["reply"] = tag_count_reply(groups)
    if queried.get("boundary"):
        grouped["boundary"] = queried["boundary"]
    return grouped


# ---------------------------------------------------------------------------
# Save view (write — delegates to operational_model_authoring.modify_operational_model)
# ---------------------------------------------------------------------------


async def save_view(
    *,
    user_id: str,
    track_id: str,
    name: str,
    view_type: str = "feed",
    config: Optional[Dict[str, Any]] = None,
    is_default: Optional[bool] = None,
    view_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Materialize a query as a saved View on a track.

    Routes through ``operational_model_authoring.modify_operational_model(action="add_view")``
    so view creation has a single implementation. The user-facing
    framing differs (the staged change kind is ``save_view`` rather
    than ``modify_operational_model.add_view``), but the underlying graph
    mutation is the same.
    """
    from app.services.operational_model_authoring import modify_operational_model

    if not track_id:
        return {"error": "missing_argument", "detail": "track_id is required"}
    if not (name or "").strip():
        return {"error": "missing_argument", "detail": "name is required"}

    if view_id:
        from app.agentive.staging_executors import _call_endpoint
        from app.api.views import update_view
        from app.models.nodes import View

        existing = await View.get(view_id)
        if existing is None or existing.track_id != track_id:
            return {"error": "not_found", "detail": "View not found on this track"}
        result = await _call_endpoint(
            update_view,
            user_id,
            view_id=view_id,
            name=name,
            view_type=view_type,
            config=config or {},
            is_default=is_default,
        )
        if isinstance(result, dict) and not result.get("error"):
            return {
                "action": "update_view",
                "view_id": view_id,
                "name": name,
                "message": f"Updated existing view '{name}' on the track.",
            }
            return result

    result = await modify_operational_model(
        user_id=user_id,
        track_id=track_id,
        action="add_view",
        name=name,
        view_type=view_type,
        config=config or {},
        is_default=bool(is_default),
    )
    # Re-message in save_view vocabulary so the agent's downstream
    # narration reads naturally.
    if not result.get("error") and result.get("view_id"):
        result["message"] = f"Saved view '{name}' on the track."
    return result
