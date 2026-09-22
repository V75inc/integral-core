"""App dashboard CRUD, widget data resolution, and template suggestions."""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, cast

from app.models.edges import CATALOGS, CONTAINS
from app.models.nodes import App, Dashboard, Track
from app.services.agent_insights import (
    activity_digest,
    count_entries_grouped,
    query_all_entries,
)
from app.services.app_graph import (
    ensure_catalog_edge,
    get_or_create_dashboards_registry,
)
from app.services.dashboard_widget_validation import normalize_widget_specs
from app.services.permissions import can_edit_app, can_view_app
from app.services.query_filters import entry_field_value, entry_matches_filters
from app.services.uniqueness import assert_unique
from app.views import dashboard_widget_types as dwt


async def _get_app_or_none(app_id: str) -> Optional[App]:
    app = await App.get(app_id)
    return app if app else None


async def _list_dashboard_nodes(app: App) -> List[Dashboard]:
    dreg = await get_or_create_dashboards_registry(app)
    children = await dreg.nodes(edge=[CATALOGS], node=["Dashboard"])
    return [cast(Dashboard, c) for c in children]


async def list_dashboards(*, user_id: str, app_id: str) -> List[Dashboard]:
    """Return dashboards visible to the caller for an App."""
    if not await can_view_app(user_id, app_id):
        return []
    app = await _get_app_or_none(app_id)
    if not app:
        return []
    boards = await _list_dashboard_nodes(app)
    boards.sort(key=lambda d: (not d.is_default, d.name or ""))
    return boards


async def get_dashboard(
    *, user_id: str, app_id: str, dashboard_id: str
) -> Optional[Dashboard]:
    """Return one dashboard when the caller may view it."""
    if not await can_view_app(user_id, app_id):
        return None
    dash = await Dashboard.get(dashboard_id)
    if not dash or dash.app_id != app_id:
        return None
    return dash


def _has_overlapping_widgets(widgets: List[Dict[str, Any]]) -> bool:
    for i, a in enumerate(widgets):
        ga = a.get("grid") or {}
        for b in widgets[i + 1 :]:
            gb = b.get("grid") or {}
            if not (
                ga.get("x", 0) + ga.get("w", 1) <= gb.get("x", 0)
                or gb.get("x", 0) + gb.get("w", 1) <= ga.get("x", 0)
                or ga.get("y", 0) + ga.get("h", 1) <= gb.get("y", 0)
                or gb.get("y", 0) + gb.get("h", 1) <= ga.get("y", 0)
            ):
                return True
    return False


def _needs_widget_reflow(widgets: List[Dict[str, Any]]) -> bool:
    if len(widgets) <= 1:
        return False
    xs = {int((w.get("grid") or {}).get("x", 0)) for w in widgets}
    return len(xs) == 1 or _has_overlapping_widgets(widgets)


def _reflow_widget_layout(
    widgets: List[Dict[str, Any]], *, columns: int = 12
) -> List[Dict[str, Any]]:
    x = 0
    y = 0
    row_h = 0
    out: List[Dict[str, Any]] = []
    for w in widgets:
        grid = dict(w.get("grid") or {})
        gw = min(max(int(grid.get("w", 4)), 1), columns)
        gh = max(int(grid.get("h", 3)), 1)
        if x > 0 and x + gw > columns:
            x = 0
            y += row_h
            row_h = 0
        grid.update({"x": x, "y": y, "w": gw, "h": gh})
        out.append({**w, "grid": grid})
        x += gw
        row_h = max(row_h, gh)
    return out


def _compact_widget_layout(
    widgets: List[Dict[str, Any]], *, columns: int = 12
) -> List[Dict[str, Any]]:
    if not widgets:
        return []
    if _needs_widget_reflow(widgets):
        widgets = _reflow_widget_layout(widgets, columns=columns)
    placed: List[Dict[str, Any]] = []
    for w in sorted(
        widgets,
        key=lambda row: (
            int((row.get("grid") or {}).get("y", 0)),
            int((row.get("grid") or {}).get("x", 0)),
        ),
    ):
        grid = dict(w.get("grid") or {})
        gx = int(grid.get("x", 0))
        gy = int(grid.get("y", 0))
        gw = int(grid.get("w", 4))
        gh = int(grid.get("h", 3))
        best_y = gy
        for try_y in range(gy + 1):
            collides = False
            for p in placed:
                pg = p.get("grid") or {}
                if not (
                    gx + gw <= int(pg.get("x", 0))
                    or int(pg.get("x", 0)) + int(pg.get("w", 0)) <= gx
                    or try_y + gh <= int(pg.get("y", 0))
                    or int(pg.get("y", 0)) + int(pg.get("h", 0)) <= try_y
                ):
                    collides = True
                    break
            if not collides:
                best_y = try_y
                break
        grid["y"] = best_y
        placed.append({**w, "grid": grid})
    return placed


def normalize_widgets_report(
    raw: Optional[List[Any]],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Normalize widgets, compact layout, and report any that were dropped."""
    widgets, dropped = normalize_widget_specs(raw)
    return _compact_widget_layout(widgets, columns=12), dropped


def _normalize_widgets(raw: Optional[List[Any]]) -> List[Dict[str, Any]]:
    widgets, dropped = normalize_widgets_report(raw)
    if dropped:
        details = "; ".join(
            f"widget[{row['index']}]: {row['reason']}" for row in dropped
        )
        raise ValueError(
            "Dashboard contains unsupported widget configuration; "
            f"nothing was saved ({details})."
        )
    return widgets


async def create_dashboard(
    *,
    user_id: str,
    app_id: str,
    name: str,
    layout: Optional[Dict[str, Any]] = None,
    widgets: Optional[List[Any]] = None,
    is_default: bool = False,
) -> Dashboard:
    """Create a dashboard under an App's Dashboards registry."""
    from app.api.validators_common import compute_fold, non_empty_after_strip

    if not await can_edit_app(user_id, app_id):
        raise PermissionError("Insufficient permissions to create dashboard")
    app = await _get_app_or_none(app_id)
    if not app:
        raise ValueError("App not found")

    clean_name = non_empty_after_strip(name, "name")
    name_fold = compute_fold(clean_name)
    await assert_unique(
        Dashboard,
        {"context.app_id": app_id, "context.name_fold": name_fold},
        entity="dashboard",
        field_label="name",
        value=clean_name,
        scope_label="in this app",
    )

    now = datetime.now(timezone.utc).isoformat()
    dreg = await get_or_create_dashboards_registry(app)
    norm_widgets = _normalize_widgets(widgets)
    norm_layout = dict(layout or {"columns": 12, "row_height": 80})

    if is_default:
        for existing in await _list_dashboard_nodes(app):
            if existing.is_default:
                existing.is_default = False
                existing.updated_at = now
                await existing.save()

    dash = await Dashboard.create(
        name=clean_name,
        name_fold=compute_fold(clean_name),
        app_id=app_id,
        workspace_id=getattr(app, "workspace_id", "") or "",
        layout=norm_layout,
        widgets=norm_widgets,
        is_default=is_default,
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )
    await ensure_catalog_edge(dreg, dash, cataloged_at=now)
    return dash


async def update_dashboard(
    *,
    user_id: str,
    app_id: str,
    dashboard_id: str,
    name: Optional[str] = None,
    layout: Optional[Dict[str, Any]] = None,
    widgets: Optional[List[Any]] = None,
    is_default: Optional[bool] = None,
) -> Dashboard:
    """Update dashboard fields the caller is permitted to edit."""
    from app.api.validators_common import compute_fold, non_empty_after_strip

    if not await can_edit_app(user_id, app_id):
        raise PermissionError("Insufficient permissions to update dashboard")
    dash = await get_dashboard(
        user_id=user_id, app_id=app_id, dashboard_id=dashboard_id
    )
    if not dash:
        raise ValueError("Dashboard not found")

    now = datetime.now(timezone.utc).isoformat()
    if name is not None:
        clean_name = non_empty_after_strip(name, "name")
        if clean_name != dash.name:
            await assert_unique(
                Dashboard,
                {
                    "context.app_id": app_id,
                    "context.name_fold": compute_fold(clean_name),
                },
                entity="dashboard",
                field_label="name",
                value=clean_name,
                scope_label="in this app",
                exclude_id=dash.id,
            )
            dash.name = clean_name
            dash.name_fold = compute_fold(clean_name)
    if layout is not None:
        dash.layout = dict(layout)
    if widgets is not None:
        dash.widgets = _normalize_widgets(widgets)
    if is_default is not None:
        if is_default:
            app = await _get_app_or_none(app_id)
            if app:
                for existing in await _list_dashboard_nodes(app):
                    if existing.id != dash.id and existing.is_default:
                        existing.is_default = False
                        existing.updated_at = now
                        await existing.save()
        dash.is_default = is_default
    dash.updated_at = now
    await dash.save()
    return dash


async def delete_dashboard(*, user_id: str, app_id: str, dashboard_id: str) -> bool:
    """Delete a dashboard when the caller may edit the App."""
    if not await can_edit_app(user_id, app_id):
        raise PermissionError("Insufficient permissions to delete dashboard")
    dash = await get_dashboard(
        user_id=user_id, app_id=app_id, dashboard_id=dashboard_id
    )
    if not dash:
        return False
    await dash.delete()
    return True


async def _app_track_ids(app: App) -> List[str]:
    """Read every app track through the graph pager, without a silent cap."""
    cursor: Optional[str] = None
    track_ids: List[str] = []
    while True:
        tracks, cursor = await app.nodes_page(
            edge=[CONTAINS], node=["Track"], cursor=cursor, limit=200
        )
        track_ids.extend(track.id for track in tracks)
        if not cursor:
            return track_ids


async def _data_source_track_ids(app: App, data_source: Dict[str, Any]) -> List[str]:
    """Resolve one dashboard source to tracks owned by its App.

    ``track_ids`` used to be accepted by the schema and then ignored by every
    resolver. Reject an out-of-app identifier so a dashboard cannot appear to
    be filtered while silently broadening to the entire App.
    """
    available = await _app_track_ids(app)
    requested: List[str] = []
    track_id = str(data_source.get("track_id") or "").strip()
    if track_id:
        requested.append(track_id)
    for candidate in data_source.get("track_ids") or []:
        value = str(candidate or "").strip()
        if value and value not in requested:
            requested.append(value)
    if not requested:
        return available
    invalid = [track_id for track_id in requested if track_id not in available]
    if invalid:
        raise ValueError(
            "dashboard data source contains tracks outside its app: "
            + ", ".join(invalid)
        )
    return requested


def _has_explicit_track_scope(data_source: Dict[str, Any]) -> bool:
    """Whether a widget requested a subset rather than its whole App."""
    return bool(
        str(data_source.get("track_id") or "").strip()
        or any(str(item or "").strip() for item in data_source.get("track_ids") or [])
    )


def _combine_track_digests(
    digests: List[Dict[str, Any]], *, period: str, workspace_id: Optional[str]
) -> Dict[str, Any]:
    """Make a selected-track digest without widening it back to the App."""
    summaries = [
        summary
        for digest in digests
        for summary in digest.get("track_summaries", [])
        if isinstance(summary, dict)
    ]
    return {
        "scope": "tracks",
        "scope_id": None,
        "workspace_id": workspace_id,
        "period": period,
        "since": digests[0].get("since") if digests else None,
        "total_tracks": sum(int(digest.get("total_tracks", 0)) for digest in digests),
        "total_entries": sum(int(digest.get("total_entries", 0)) for digest in digests),
        "recent_entry_count": sum(
            int(digest.get("recent_entry_count", 0)) for digest in digests
        ),
        "track_summaries": summaries,
    }


async def _resolve_activity_digest(
    *,
    user_id: str,
    app_id: str,
    workspace_id: Optional[str],
    data_source: Dict[str, Any],
) -> Dict[str, Any]:
    """Resolve an activity widget while honouring its declared track scope."""
    period = data_source.get("period") or "week"
    app = await _get_app_or_none(app_id)
    if not app:
        return {
            "scope": "app",
            "scope_id": app_id,
            "workspace_id": workspace_id,
            "period": period,
            "total_tracks": 0,
            "total_entries": 0,
            "recent_entry_count": 0,
            "track_summaries": [],
        }
    selected = await _data_source_track_ids(app, data_source)
    if not _has_explicit_track_scope(data_source):
        return await activity_digest(
            user_id=user_id,
            scope="app",
            scope_id=app_id,
            period=period,
            workspace_id=workspace_id,
        )
    digests = [
        await activity_digest(
            user_id=user_id,
            scope="track",
            scope_id=track_id,
            period=period,
            workspace_id=workspace_id,
        )
        for track_id in selected
    ]
    return _combine_track_digests(digests, period=period, workspace_id=workspace_id)


async def _resolve_count(
    *,
    user_id: str,
    app_id: str,
    workspace_id: Optional[str],
    data_source: Dict[str, Any],
) -> Dict[str, Any]:
    # Profile-field filters cannot be represented by the legacy platform-status
    # query arguments. Resolve the visible records and apply typed filters.
    if data_source.get("filters") or data_source.get("track_ids"):
        entries, _total = await _collect_data_source_entries(
            user_id=user_id,
            app_id=app_id,
            workspace_id=workspace_id,
            data_source=data_source,
        )
        return {"value": len(entries), "total_matched": len(entries)}
    track_id = data_source.get("track_id")
    if track_id:
        result = await query_all_entries(
            user_id=user_id,
            track_id=track_id,
            status=data_source.get("status"),
            statuses=data_source.get("statuses"),
            tags=data_source.get("tags"),
            entry_type=data_source.get("entry_type"),
            since=data_source.get("since"),
            until=data_source.get("until"),
            workspace_id=workspace_id,
        )
        return {
            "value": result.get("total", 0),
            "total_matched": result.get("total", 0),
        }

    app = await _get_app_or_none(app_id)
    if not app:
        return {"value": 0, "total_matched": 0}
    total = 0
    for tid in await _data_source_track_ids(app, data_source):
        result = await query_all_entries(
            user_id=user_id,
            track_id=tid,
            status=data_source.get("status"),
            statuses=data_source.get("statuses"),
            tags=data_source.get("tags"),
            entry_type=data_source.get("entry_type"),
            since=data_source.get("since"),
            until=data_source.get("until"),
            workspace_id=workspace_id,
        )
        total += int(result.get("total", 0))
    return {"value": total, "total_matched": total}


async def _collect_app_entries(
    *,
    user_id: str,
    app_id: str,
    workspace_id: Optional[str],
    data_source: Dict[str, Any],
) -> tuple[List[Dict[str, Any]], int]:
    """Gather entries across every track in an App (dashboard scope)."""
    app = await _get_app_or_none(app_id)
    if not app:
        return [], 0
    entries: List[Dict[str, Any]] = []
    total = 0
    for tid in await _data_source_track_ids(app, data_source):
        result = await query_all_entries(
            user_id=user_id,
            track_id=tid,
            status=data_source.get("status"),
            statuses=data_source.get("statuses"),
            tags=data_source.get("tags"),
            entry_type=data_source.get("entry_type"),
            since=data_source.get("since"),
            until=data_source.get("until"),
            workspace_id=workspace_id,
        )
        entries.extend(result.get("entries", []))
        total += int(result.get("total", 0))
    filtered = _apply_profile_filters(entries, data_source.get("filters"))
    return filtered, len(filtered) if data_source.get("filters") else total


def _entry_path_value(entry: Dict[str, Any], path: str) -> Any:
    """Compatibility wrapper around the shared operational field resolver."""
    return entry_field_value(entry, path)


def _apply_profile_filters(
    entries: List[Dict[str, Any]], filters: Any
) -> List[Dict[str, Any]]:
    """Apply the same explicit filter expressions used by governed queries."""
    return [entry for entry in entries if entry_matches_filters(entry, filters)]


async def _collect_data_source_entries(
    *,
    user_id: str,
    app_id: str,
    workspace_id: Optional[str],
    data_source: Dict[str, Any],
) -> tuple[List[Dict[str, Any]], int]:
    """Collect one dashboard source at a track or its containing App."""
    track_id = data_source.get("track_id")
    if track_id and not data_source.get("track_ids"):
        app = await _get_app_or_none(app_id)
        if not app:
            return [], 0
        # Validate the narrow source before querying it. A caller with access
        # to a track in another App must not be able to graft it onto this
        # dashboard by configuration.
        selected = await _data_source_track_ids(app, data_source)
        result = await query_all_entries(
            user_id=user_id,
            track_id=selected[0],
            status=data_source.get("status"),
            statuses=data_source.get("statuses"),
            tags=data_source.get("tags"),
            entry_type=data_source.get("entry_type"),
            since=data_source.get("since"),
            until=data_source.get("until"),
            workspace_id=workspace_id,
        )
        rows = _apply_profile_filters(
            list(result.get("entries", [])), data_source.get("filters")
        )
        return rows, len(rows)
    return await _collect_app_entries(
        user_id=user_id,
        app_id=app_id,
        workspace_id=workspace_id,
        data_source=data_source,
    )


def _group_entries(
    entries: List[Dict[str, Any]],
    group_by: str,
) -> List[Dict[str, Any]]:
    counter: Counter[str] = Counter()
    if group_by == "track":
        for entry in entries:
            counter[entry.get("track_id") or "(unknown)"] += 1
    elif group_by == "status":
        for entry in entries:
            counter[entry.get("status") or "(none)"] += 1
    elif group_by.startswith("custom_fields."):
        for entry in entries:
            value = _entry_path_value(entry, group_by)
            counter[str(value) if value not in (None, "") else "(none)"] += 1
    elif group_by == "tag":
        for entry in entries:
            for tag in entry.get("tags", []) or []:
                counter[tag] += 1
    elif group_by == "entry_type":
        for entry in entries:
            counter[entry.get("type_id") or "(none)"] += 1
    elif group_by == "date":
        for entry in entries:
            raw = entry.get("created_at")
            bucket = str(raw)[:10] if raw else "(unknown)"
            counter[bucket] += 1
    else:
        return []
    return [
        {"key": key, "label": key, "count": count}
        for key, count in counter.most_common()
    ]


async def _resolve_grouped_chart(
    *,
    user_id: str,
    workspace_id: Optional[str],
    data_source: Dict[str, Any],
    app_id: Optional[str] = None,
) -> Dict[str, Any]:
    group_by = data_source.get("group_by") or "status"
    is_profile_group = group_by.startswith("custom_fields.")
    if (
        group_by not in ("track", "status", "tag", "entry_type", "date")
        and not is_profile_group
    ):
        return {"error": "invalid_group_by", "group_by": group_by}
    track_id = data_source.get("track_id")

    if track_id and not is_profile_group and not data_source.get("filters"):
        raw = await count_entries_grouped(
            user_id=user_id,
            group_by=cast(
                Literal["track", "status", "tag", "entry_type", "date"], group_by
            ),
            track_id=track_id,
            status=data_source.get("status"),
            statuses=data_source.get("statuses"),
            tags=data_source.get("tags"),
            entry_type=data_source.get("entry_type"),
            since=data_source.get("since"),
            until=data_source.get("until"),
            workspace_id=workspace_id,
        )
        series = [
            {"label": g.get("label") or g.get("key"), "value": g.get("count", 0)}
            for g in raw.get("groups", [])
        ]
        if not series and app_id:
            entries, total = await _collect_app_entries(
                user_id=user_id,
                app_id=app_id,
                workspace_id=workspace_id,
                data_source=data_source,
            )
            groups = _group_entries(entries, group_by)
            raw = {"groups": groups, "total_matched": total, "group_by": group_by}
        else:
            return {
                "series": series,
                "group_by": group_by,
                "total_matched": raw.get("total_matched", 0),
            }
    elif app_id:
        entries, total = await _collect_data_source_entries(
            user_id=user_id,
            app_id=app_id,
            workspace_id=workspace_id,
            data_source=data_source,
        )
        groups = _group_entries(entries, group_by)
        if group_by == "track":
            from app.models.nodes import Track

            track_titles: Dict[str, str] = {}
            for group in groups:
                tid = group["key"]
                if tid == "(unknown)":
                    continue
                try:
                    track = await Track.get(tid)
                    if track:
                        track_titles[tid] = getattr(track, "title", tid)
                except Exception:  # noqa: BLE001
                    continue
            for group in groups:
                tid = group["key"]
                group["label"] = track_titles.get(tid, tid)
        raw = {
            "groups": groups,
            "total_matched": total,
            "group_by": group_by,
        }
    else:
        raw = await count_entries_grouped(
            user_id=user_id,
            group_by=cast(
                Literal["track", "status", "tag", "entry_type", "date"], group_by
            ),
            track_id=None,
            status=data_source.get("status"),
            statuses=data_source.get("statuses"),
            tags=data_source.get("tags"),
            entry_type=data_source.get("entry_type"),
            since=data_source.get("since"),
            until=data_source.get("until"),
            workspace_id=workspace_id,
        )
    if not isinstance(raw, dict):
        raw = {}
    groups_raw = raw.get("groups", [])
    groups = groups_raw if isinstance(groups_raw, list) else []
    series = [
        {
            "label": (g.get("label") or g.get("key")) if isinstance(g, dict) else "",
            "value": g.get("count", 0) if isinstance(g, dict) else 0,
        }
        for g in groups
        if isinstance(g, dict)
    ]
    return {
        "series": series,
        "group_by": group_by,
        "total_matched": raw.get("total_matched", 0),
    }


async def resolve_widget_data(
    *,
    user_id: str,
    app_id: str,
    widget: Dict[str, Any],
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve payload data for a single dashboard widget."""
    wtype = str(widget.get("type") or "")
    ds = dict(widget.get("data_source") or {})
    ds.setdefault("kind", "count")

    if wtype == "metric_card":
        if ds.get("metric") == "track_count":
            app = await _get_app_or_none(app_id)
            count = len(await _app_track_ids(app)) if app else 0
            return {"value": count, "total_matched": count}
        return await _resolve_count(
            user_id=user_id, app_id=app_id, workspace_id=workspace_id, data_source=ds
        )

    if wtype == "metric_row":
        metrics_out = []
        for m in ds.get("metrics") or []:
            row = await _resolve_count(
                user_id=user_id,
                app_id=app_id,
                workspace_id=workspace_id,
                data_source=dict(m),
            )
            metrics_out.append(
                {
                    "label": m.get("label") or "",
                    "value": row.get("value", 0),
                }
            )
        return {"metrics": metrics_out}

    if wtype in ("chart_bar", "chart_line", "chart_pie"):
        ds["kind"] = "grouped_count"
        # chart_line is time-series only — coerce any categorical group_by.
        if wtype == "chart_line":
            ds["group_by"] = "date"
        return await _resolve_grouped_chart(
            user_id=user_id,
            workspace_id=workspace_id,
            data_source=ds,
            app_id=app_id,
        )

    if wtype == "activity_digest":
        raw = await _resolve_activity_digest(
            user_id=user_id,
            app_id=app_id,
            workspace_id=workspace_id,
            data_source=ds,
        )
        raw["tracks"] = raw.get("track_summaries", [])
        return raw

    if wtype == "recent_entries":
        limit = int(ds.get("limit") or 10)
        all_entries, _total = await _collect_data_source_entries(
            user_id=user_id,
            app_id=app_id,
            workspace_id=workspace_id,
            data_source={**ds, "limit": limit},
        )
        all_entries.sort(
            key=lambda e: e.get("updated_at") or e.get("created_at") or "",
            reverse=True,
        )
        return {"entries": all_entries[:limit]}

    if wtype == "track_breakdown":
        digest = await _resolve_activity_digest(
            user_id=user_id,
            app_id=app_id,
            workspace_id=workspace_id,
            data_source={**ds, "period": ds.get("period") or "month"},
        )
        return {"tracks": digest.get("track_summaries", [])}

    return {"error": "unsupported_widget", "type": wtype}


async def resolve_dashboard_data(
    *,
    user_id: str,
    app_id: str,
    dashboard: Dashboard,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve live data for every widget on a dashboard."""
    out: Dict[str, Any] = {}
    for widget in dashboard.widgets or []:
        wid = widget.get("id")
        if not wid:
            continue
        try:
            out[wid] = await resolve_widget_data(
                user_id=user_id,
                app_id=app_id,
                widget=widget,
                workspace_id=workspace_id,
            )
        except Exception as exc:  # noqa: BLE001
            out[wid] = {"error": str(exc)}
    return out


async def suggest_dashboard_template(
    *,
    user_id: str,
    app_id: str,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Heuristic starter dashboard for vague agent/user requests."""
    if not await can_view_app(user_id, app_id):
        return {"error": "access_denied"}
    app = await _get_app_or_none(app_id)
    if not app:
        return {"error": "app_not_found"}

    tracks = await app.nodes(edge=[CONTAINS], node=["Track"])
    track_list = [cast(Track, t) for t in tracks]
    primary_track_id = track_list[0].id if track_list else None

    digest = await activity_digest(
        user_id=user_id,
        scope="app",
        scope_id=app_id,
        period="week",
        workspace_id=workspace_id,
    )

    widgets: List[Dict[str, Any]] = []
    y = 0

    def _add(
        wtype: str,
        title: str,
        *,
        x: int = 0,
        w: int = 4,
        h: int = 3,
        data_source: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        nonlocal y
        spec = dwt.get_spec(wtype)
        default = (spec.default_size if spec else {}) or {"w": w, "h": h}
        widgets.append(
            {
                "id": f"w_{uuid.uuid4().hex[:8]}",
                "type": wtype,
                "title": title,
                "grid": {
                    "x": x,
                    "y": y,
                    "w": int(default.get("w", w)),
                    "h": int(default.get("h", h)),
                },
                "config": dict(config or {}),
                "data_source": dict(data_source or {}),
            }
        )
        if x + int(default.get("w", w)) >= 12:
            y += int(default.get("h", h))

    total_entries = digest.get("total_entries", 0)
    # A starter dashboard should describe the user's operation, not Integral's
    # internals.  The first named tracks are the only universally available
    # domain signal, so make their record counts the headline metrics instead
    # of generic "entries" and platform lifecycle status.
    for index, track in enumerate(track_list[:3]):
        _add(
            "metric_card",
            f"{track.title} records",
            x=index * 4,
            w=4,
            h=2,
            data_source={"kind": "count", "track_id": track.id},
        )

    if len(track_list) <= 1 and primary_track_id:
        _add(
            "chart_bar",
            "Entries by status",
            x=0,
            w=6,
            h=4,
            data_source={
                "kind": "grouped_count",
                "track_id": primary_track_id,
                "group_by": "status",
            },
        )
        _add(
            "recent_entries",
            "Recent activity",
            x=6,
            w=6,
            h=4,
            data_source={"limit": 8},
        )
    else:
        _add(
            "track_breakdown",
            "Tracks overview",
            x=0,
            w=6,
            h=4,
            data_source={"kind": "track_breakdown", "period": "week"},
        )
        _add(
            "activity_digest",
            "Recent activity",
            x=6,
            w=6,
            h=4,
            data_source={"kind": "activity_digest", "period": "week"},
        )
    rationale = (
        f"Suggested layout for **{app.name}** with {len(track_list)} track(s) "
        f"and {total_entries} total entries, led by the app's named operating areas."
    )
    return {
        "name": f"{app.name} Overview",
        "layout": {"columns": 12, "row_height": 80},
        "widgets": _compact_widget_layout(widgets, columns=12),
        "rationale": rationale,
    }
