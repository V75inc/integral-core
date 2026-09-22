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


async def query_entries(
    *,
    user_id: str,
    track_id: Optional[str] = None,
    query: Optional[str] = None,
    status: Optional[str] = None,
    statuses: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    entry_type: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    sort_by: SortBy = "updated_at",
    sort_dir: SortDir = "desc",
    limit: int = 20,
    offset: int = 0,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Filtered cross-track or track-scoped entry query.

    Pulls all accessible entries for the user (per
    ``app.services.permissions``), then applies the filter pipeline
    in-process. Sufficient for moderate workspaces; if performance
    becomes an issue we'd push filters into the storage layer instead.
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
                    "since": since,
                    "until": until,
                    "query": query or None,
                },
            }

    # Gather candidate entries.
    if track_id:
        entries = await get_user_accessible_entries(user_id, track_id, strict=True)
    else:
        entries = []
        for t in accessible_tracks:
            entries.extend(
                await get_user_accessible_entries(user_id, t.id, strict=True)
            )

    # Filter pipeline.
    from app.services.retrieval.keyword_match import matches_keywords, tokenize

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
    # Now we accept EITHER an exact type_id (legacy) OR a name
    # (case- and singular/plural-insensitive). Names resolve once
    # per call by loading EntryType nodes referenced on the
    # candidate entries — cheap because there are few EntryTypes
    # per workspace.
    accept_type_ids: Optional[set] = None
    if entry_type:
        et_filter = entry_type.strip()
        if et_filter:
            # If it's already an id (matches at least one entry's
            # type_id verbatim), use the literal-id path.
            candidate_type_ids = {
                getattr(e, "type_id", "") for e in entries if getattr(e, "type_id", "")
            }
            if et_filter in candidate_type_ids:
                accept_type_ids = {et_filter}
            else:
                # Resolve names. Build a type_id → name map and
                # accept any whose name matches the filter
                # case-insensitively. Tolerant of trailing 's' so
                # "opportunity" matches an "Opportunity" type.
                from app.models.nodes import EntryType

                name_fold = et_filter.casefold().rstrip("s")
                accept_type_ids = set()
                # Dedup load per type_id.
                for tid in candidate_type_ids:
                    try:
                        et = await EntryType.get(tid)
                    except Exception:
                        et = None
                    if not et:
                        continue
                    et_name = (getattr(et, "name", "") or "").casefold().rstrip("s")
                    if et_name and (et_name == name_fold or name_fold in et_name):
                        accept_type_ids.add(tid)

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

    # Sort.
    reverse = sort_dir == "desc"
    if sort_by == "title":
        filtered.sort(
            key=lambda e: (getattr(e, "title", "") or "").casefold(), reverse=reverse
        )
    elif sort_by == "created_at":
        filtered.sort(
            key=lambda e: getattr(e, "created_at", "") or "",
            reverse=reverse,
        )
    else:  # updated_at, default
        filtered.sort(
            key=lambda e: getattr(e, "updated_at", "")
            or getattr(e, "created_at", "")
            or "",
            reverse=reverse,
        )

    total = len(filtered)
    sliced = filtered[offset : offset + limit]

    return {
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
            "since": since,
            "until": until,
            "query": query or None,
        },
    }


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

    track_summaries: List[Dict[str, Any]] = []
    total_entries = 0
    recent_entry_count = 0
    for t in tracks:
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
        total_entries += len(entries)
        recent_entry_count += len(recent)
        track_summaries.append(
            {
                "track_id": t.id,
                "title": getattr(t, "title", ""),
                "entry_count": len(entries),
                "recent_count": len(recent),
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

    return {
        "scope": scope,
        "scope_id": scope_id,
        "workspace_id": workspace_id,
        "period": period,
        "since": since,
        "total_tracks": len(tracks),
        "total_entries": total_entries,
        "recent_entry_count": recent_entry_count,
        "track_summaries": track_summaries,
    }


# ---------------------------------------------------------------------------
# Count (group-by)
# ---------------------------------------------------------------------------


GroupBy = Literal["track", "status", "tag", "entry_type", "date"]


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
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Count entries grouped by the given dimension.

    Applies the same filter pipeline as ``query_entries``, including
    the B-AGENT-03 workspace gate when ``workspace_id`` is set.
    """
    # Reuse the query helper to apply filters (with high limit so we
    # see all matches), then aggregate.
    queried = await query_all_entries(
        user_id=user_id,
        track_id=track_id,
        status=status,
        statuses=statuses,
        tags=tags,
        entry_type=entry_type,
        since=since,
        until=until,
        workspace_id=workspace_id,
    )
    entries = queried.get("entries", [])

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
                counter[tag] += 1
        groups = [{"key": t, "label": t, "count": n} for t, n in counter.most_common()]
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

    return {
        "group_by": group_by,
        "total_matched": queried.get("total", 0),
        "groups": groups,
        "filters_applied": queried.get("filters_applied", {}),
    }


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

    result = await modify_operational_model(
        user_id=user_id,
        track_id=track_id,
        action="add_view",
        name=name,
        view_type=view_type,
        config=config or {},
    )
    # Re-message in save_view vocabulary so the agent's downstream
    # narration reads naturally.
    if not result.get("error") and result.get("view_id"):
        result["message"] = f"Saved view '{name}' on the track."
    return result
