"""App dashboard CRUD, widget data resolution, and template suggestions."""

from __future__ import annotations

import logging
import re
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional, cast
from zoneinfo import ZoneInfo

from app.models.edges import CATALOGS, CONTAINS
from app.models.nodes import App, Dashboard, Track
from app.schemas.entry_aggregate import AggregateSpec
from app.schemas.query_spec import QuerySpec
from app.services.agent_insights import (
    activity_digest,
    count_entries_grouped,
    query_all_entries,
)
from app.services.app_graph import (
    ensure_catalog_edge,
    get_or_create_dashboards_registry,
    get_track_attached_operational_model,
)
from app.services.dashboard_widget_validation import normalize_widget_specs
from app.services.entry_aggregate import aggregate_rows
from app.services.permissions import can_edit_app, can_view_app
from app.services.query_filters import entry_field_value, entry_matches_filters
from app.services.uniqueness import assert_unique
from app.views import dashboard_widget_types as dwt

_NON_ADDITIVE_FIELD_TOKENS = {
    "cap",
    "ceiling",
    "floor",
    "index",
    "offset",
    "pct",
    "percent",
    "percentage",
    "probability",
    "rank",
    "rate",
    "ratio",
    "score",
}
_ADDITIVE_FIELD_TOKENS = {
    "allowance",
    "amount",
    "balance",
    "bonus",
    "charge",
    "child",
    "children",
    "commission",
    "compensation",
    "cost",
    "count",
    "credit",
    "debit",
    "deduction",
    "deposit",
    "expense",
    "fee",
    "gross",
    "headcount",
    "income",
    "net",
    "nis",
    "overtime",
    "paye",
    "payment",
    "price",
    "quantity",
    "revenue",
    "salary",
    "spend",
    "subtotal",
    "tax",
    "total",
    "unit",
    "units",
    "value",
    "wage",
}
_DUE_DATE_TOKENS = {"deadline", "due", "expires", "expiration", "expiry"}
_CONFIGURATION_TRACK_TOKENS = {
    "config",
    "configuration",
    "rate",
    "rates",
    "setting",
    "settings",
}
_logger = logging.getLogger(__name__)


def _dashboard_field_tokens(field: Dict[str, Any]) -> set[str]:
    raw = f"{field.get('key') or ''} {field.get('name') or ''}".casefold()
    return set(re.findall(r"[a-z0-9]+", raw))


def _is_dashboard_sum_candidate(field: Dict[str, Any]) -> bool:
    """Avoid totals for numeric values that are ratios, offsets, or limits."""
    field_type = str(field.get("type") or "").casefold()
    tokens = _dashboard_field_tokens(field)
    if tokens & _NON_ADDITIVE_FIELD_TOKENS:
        return False
    if field_type in {"currency", "money", "duration"}:
        return True
    return field_type == "number" and bool(tokens & _ADDITIVE_FIELD_TOKENS)


def _is_configuration_track(track: Track) -> bool:
    tokens = set(re.findall(r"[a-z0-9]+", str(track.title or "").casefold()))
    return bool(tokens & _CONFIGURATION_TRACK_TOKENS)


def _is_due_date_field(field: Dict[str, Any]) -> bool:
    return bool(_dashboard_field_tokens(field) & _DUE_DATE_TOKENS)


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


async def _track_dashboard_fields(track: Track) -> List[Dict[str, Any]]:
    """Read fields from the Track's attached model without using App guesses."""
    try:
        operational_model = await get_track_attached_operational_model(track)
    except Exception:  # noqa: BLE001 — legacy/untyped Tracks have no attached model
        return []
    if operational_model is None:
        return []
    try:
        entry_types = await operational_model.nodes(
            edge=[CONTAINS], node=["EntryType"], limit=200
        )
    except TypeError:
        entry_types = await operational_model.nodes(edge=[CONTAINS], node=["EntryType"])
    fields: Dict[str, Dict[str, Any]] = {}
    for entry_type in entry_types:
        schema = getattr(entry_type, "form_schema", {})
        source_fields = schema.get("fields", []) if isinstance(schema, dict) else []
        for field in source_fields if isinstance(source_fields, list) else []:
            if not isinstance(field, dict):
                continue
            key = str(field.get("key") or "").strip()
            field_type = str(field.get("type") or "").strip().lower()
            if key and field_type:
                fields.setdefault(
                    key,
                    {
                        "key": key,
                        "name": str(field.get("name") or key),
                        "type": field_type,
                        "enum": list(field.get("enum") or []),
                    },
                )
    return list(fields.values())


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


async def validate_dashboard_field_bindings(
    *, user_id: str, app_id: str, widgets: List[Dict[str, Any]]
) -> None:
    """Reject business-field bindings absent from visible published schemas."""
    from app.services.permissions import can_view_track

    app = await _get_app_or_none(app_id)
    if app is None or not await can_view_app(user_id, app_id):
        raise PermissionError("Dashboard source is unavailable")
    fields_by_track: Dict[str, set[str]] = {}
    for widget in widgets:
        source = widget.get("data_source") or {}
        if source.get("kind") == "declared_query":
            # Packaged Apps own their declared query schema and authorization.
            continue
        requested: set[str] = set()
        value_field = str(source.get("field") or "")
        if value_field:
            requested.add(value_field.removeprefix("custom_fields."))
        group = str(source.get("group_by") or "")
        if group.startswith("date:"):
            group = group[5:]
            if group not in {"created_at", "updated_at"}:
                requested.add(group.removeprefix("custom_fields."))
        elif group.startswith("custom_fields."):
            requested.add(group[len("custom_fields.") :])
        for predicate in source.get("filters") or []:
            field = str(predicate.get("field") or "")
            if field.startswith("custom_fields."):
                requested.add(field[len("custom_fields.") :])
        if not requested:
            continue
        available: set[str] = set()
        for track_id in await _data_source_track_ids(app, source):
            if track_id not in fields_by_track:
                track = await Track.get(track_id)
                if track is None or not await can_view_track(user_id, track_id):
                    raise PermissionError("Dashboard source is unavailable")
                fields_by_track[track_id] = {
                    field["key"] for field in await _track_dashboard_fields(track)
                }
            available.update(fields_by_track[track_id])
        # Schema-less Core tracks support arbitrary custom fields. Their
        # governed query boundary remains authoritative; only a declared
        # published field contract can reject an unknown schema binding.
        if not available:
            continue
        missing = requested - available
        if missing:
            raise ValueError(
                "Dashboard fields are not available in the selected published model: "
                + ", ".join(sorted(missing))
            )


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
    await validate_dashboard_field_bindings(
        user_id=user_id, app_id=app_id, widgets=norm_widgets
    )
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
        normalized = _normalize_widgets(widgets)
        await validate_dashboard_field_bindings(
            user_id=user_id, app_id=app_id, widgets=normalized
        )
        dash.widgets = normalized
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


async def build_dashboard_drilldown_spec(
    *, app: App, widget: Dict[str, Any], group_key: Optional[str], cursor: Optional[str]
) -> QuerySpec:
    """Compile a dashboard widget's exact predicate into a governed query."""
    source = dict(widget.get("data_source") or {})
    track_ids = await _data_source_track_ids(app, source)
    if not track_ids:
        raise ValueError("dashboard widget has no in-App Track scope")
    if len(track_ids) > 100:
        raise ValueError("dashboard drill-through supports at most 100 Tracks")

    filters: List[Dict[str, Any]] = [
        {
            "field": "track_id",
            "op": "eq" if len(track_ids) == 1 else "in",
            "value": track_ids[0] if len(track_ids) == 1 else track_ids,
        }
    ]
    if source.get("status"):
        filters.append({"field": "status", "op": "eq", "value": source["status"]})
    if source.get("statuses"):
        filters.append({"field": "status", "op": "in", "value": source["statuses"]})
    if source.get("tags"):
        if len(source["tags"]) != 1:
            raise ValueError(
                "drill-through cannot exactly represent an any-of tag filter"
            )
        filters.append({"field": "tags", "op": "contains", "value": source["tags"][0]})
    if source.get("entry_type"):
        filters.append({"field": "type_id", "op": "eq", "value": source["entry_type"]})
    if source.get("since"):
        filters.append({"field": "created_at", "op": "gte", "value": source["since"]})
    if source.get("until"):
        filters.append({"field": "created_at", "op": "lte", "value": source["until"]})

    select = ["id", "title", "track_id"]
    for raw_filter in source.get("filters") or []:
        item = (
            raw_filter.model_dump()
            if hasattr(raw_filter, "model_dump")
            else dict(raw_filter)
        )
        field = str(item.get("field") or "").strip()
        op = str(item.get("op") or "eq")
        value = item.get("value")
        if op == "neq":
            op = "ne"
        elif op == "exists":
            op, value = "is_null", not bool(value)
        filters.append({"field": field, "op": op, "value": value})
        if field.startswith("custom_fields.") and field not in select:
            select.append(field)

    if group_key is not None:
        group_by = str(source.get("group_by") or "")
        date_field = ""
        if group_by == "date":
            date_field = "created_at"
        elif group_by.startswith("date:"):
            date_field = group_by.split(":", 1)[1].strip()
            if not date_field.startswith("custom_fields.") and date_field not in {
                "created_at",
                "updated_at",
            }:
                date_field = f"custom_fields.{date_field}"
        if date_field:
            try:
                local_day = datetime.fromisoformat(group_key).date()
                zone = ZoneInfo(str(source.get("timezone") or "UTC"))
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(
                    "date drill-through group must be an ISO date"
                ) from exc
            start = datetime.combine(local_day, datetime.min.time(), tzinfo=zone)
            end = datetime.combine(
                local_day + timedelta(days=1), datetime.min.time(), tzinfo=zone
            )
            filters.extend(
                [
                    {
                        "field": date_field,
                        "op": "gte",
                        "value": start.astimezone(timezone.utc).isoformat(),
                    },
                    {
                        "field": date_field,
                        "op": "lt",
                        "value": end.astimezone(timezone.utc).isoformat(),
                    },
                ]
            )
            if date_field.startswith("custom_fields.") and date_field not in select:
                select.append(date_field)
        elif group_by in {"track", "status", "tag", "entry_type"}:
            field = {"track": "track_id", "entry_type": "type_id"}.get(
                group_by, group_by
            )
            is_empty = group_key in {"(none)", "(unknown)"}
            filters.append(
                {
                    "field": field,
                    "op": "is_null" if is_empty else "eq",
                    "value": True if is_empty else group_key,
                }
            )
            if field == "status" and field not in select:
                select.append(field)
        else:
            field = (
                group_by
                if group_by.startswith("custom_fields.")
                else f"custom_fields.{group_by}"
            )
            is_empty = group_key in {"(none)", "(unknown)"}
            filters.append(
                {
                    "field": field,
                    "op": "is_null" if is_empty else "eq",
                    "value": True if is_empty else group_key,
                }
            )
            if field not in select:
                select.append(field)

    value_field = str(source.get("field") or "").strip()
    if value_field:
        value_path = (
            value_field
            if value_field.startswith("custom_fields.")
            else f"custom_fields.{value_field}"
        )
        if value_path not in select:
            select.append(value_path)
    if len(select) > 20:
        raise ValueError(
            "dashboard widget has too many selected fields for drill-through"
        )
    if len(filters) > 8:
        raise ValueError(
            "dashboard widget has too many filters for governed drill-through"
        )
    query_cost_per_entry = len(select) + 2 * len(filters) + 2
    limit = min(100, max(1, 1000 // query_cost_per_entry))
    return QuerySpec(
        resource="entry",
        select=select,
        filters=filters,
        sort=[{"field": "updated_at", "direction": "desc"}],
        limit=limit,
        cost_ceiling=1000,
        cursor=cursor,
    )


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
    app = await _get_app_or_none(app_id)
    if app is not None:
        from app.services.query_boundary import decide_app

        decision = decide_app(app)
        if not decision.allowed:
            return {
                "value": None,
                "total_matched": 0,
                "refused": decision.public(app_id=app_id),
            }
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


async def _resolve_aggregate_data_source(
    *,
    user_id: str,
    app_id: str,
    workspace_id: Optional[str],
    data_source: Dict[str, Any],
) -> Dict[str, Any]:
    """Resolve a dashboard aggregate over exactly the selected App tracks.

    Each track query is independently authorized by the shared query path. Any
    incomplete or refused page set aborts the aggregate so a partial total can
    never be presented as an exact KPI.
    """
    app = await _get_app_or_none(app_id)
    if app is None:
        return {"value": None, "error": "app_not_found"}
    track_ids = await _data_source_track_ids(app, data_source)
    rows: List[Dict[str, Any]] = []
    for track_id in track_ids:
        result = await query_all_entries(
            user_id=user_id,
            track_id=track_id,
            status=data_source.get("status"),
            statuses=data_source.get("statuses"),
            tags=data_source.get("tags"),
            entry_type=data_source.get("entry_type"),
            filters=data_source.get("filters"),
            since=data_source.get("since"),
            until=data_source.get("until"),
            workspace_id=workspace_id,
        )
        if result.get("error") or result.get("refused"):
            return {
                "value": None,
                "error": result.get("error", "query_refused"),
                **({"refused": result["refused"]} if result.get("refused") else {}),
            }
        if result.get("complete") is False:
            return {"value": None, "error": "incomplete_query"}
        rows.extend(result.get("entries") or [])

    spec = AggregateSpec(
        op=data_source.get("op") or "count",
        field=data_source.get("field") or "",
        group_by=data_source.get("group_by") or "",
        timezone=data_source.get("timezone") or "UTC",
        scale=data_source.get("scale"),
        budget=data_source.get("budget", 5000),
    )
    aggregate = aggregate_rows(rows, spec)
    if aggregate.get("error"):
        return {"value": None, **aggregate}
    groups = aggregate.get("groups") or []
    return {
        **aggregate,
        "total_matched": len(rows),
        "series": [
            {"label": group.get("key", ""), "value": group.get("value")}
            for group in groups
            if isinstance(group, dict)
        ],
    }


def _json_path_value(value: Any, path: str) -> Any:
    """Read a conservative dotted property path from a JSON-shaped value."""
    current = value
    parts = [part.strip() for part in str(path or "").split(".")]
    if not parts or any(not part or part.startswith("_") for part in parts):
        return None
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _json_schema_path(schema: Any, path: str) -> Optional[Dict[str, Any]]:
    """Resolve a dotted property in a JSON Schema object declaration."""
    current = schema
    parts = [part.strip() for part in str(path or "").split(".")]
    if not parts or any(not part or part.startswith("_") for part in parts):
        return None
    for part in parts:
        if not isinstance(current, dict) or current.get("type") != "object":
            return None
        properties = current.get("properties")
        if not isinstance(properties, dict):
            return None
        current = properties.get(part)
    return current if isinstance(current, dict) else None


def _declared_query_output_paths(
    query_spec: Dict[str, Any], data_source: Dict[str, Any]
) -> Optional[tuple[str, str]]:
    """Require the query to declare complete rows and a matching total."""
    output_schema = query_spec.get("output_schema")
    if not isinstance(output_schema, dict):
        return None
    rows_path = str(data_source.get("rows_path") or "").strip()
    total_path = str(data_source.get("total_path") or "").strip()
    rows_schema = _json_schema_path(output_schema, rows_path)
    total_schema = _json_schema_path(output_schema, total_path)
    if (
        not rows_schema
        or rows_schema.get("type") != "array"
        or not isinstance(rows_schema.get("items"), dict)
        or rows_schema["items"].get("type") != "object"
        or not total_schema
        or total_schema.get("type") not in {"integer", "number"}
    ):
        return None
    item_properties = rows_schema["items"].get("properties") or {}
    field = str(data_source.get("field") or "").strip()
    if field.startswith("custom_fields."):
        field = field[len("custom_fields.") :]
    if field and field not in item_properties:
        return None
    group_by = str(data_source.get("group_by") or "").strip()
    if group_by == "date":
        group_field = "created_at"
    elif group_by.startswith("date:"):
        group_field = group_by.split(":", 1)[1].strip()
        if group_field.startswith("custom_fields."):
            group_field = group_field[len("custom_fields.") :]
    else:
        group_field = group_by
        if group_field.startswith("custom_fields."):
            group_field = group_field[len("custom_fields.") :]
    if group_field and group_field not in item_properties:
        return None
    return rows_path, total_path


async def _resolve_declared_query_aggregate(
    *,
    user_id: str,
    app_id: str,
    workspace_id: Optional[str],
    data_source: Dict[str, Any],
) -> Dict[str, Any]:
    """Aggregate an App query's explicitly declared, complete result rows."""
    from app.services.app_queries.dispatch import invoke_app_query
    from app.services.app_queries.registry import get_app_query

    query_key = str(data_source.get("query_key") or "").strip()
    if not workspace_id or not query_key:
        return {"value": None, "error": "declared_query_unavailable"}
    query = get_app_query(workspace_id, app_id, query_key)
    if query is None:
        return {"value": None, "error": "declared_query_not_found"}
    contract = query.get("dashboard")
    if not isinstance(contract, dict):
        return {"value": None, "error": "declared_query_not_dashboard_enabled"}
    # Paths and inputs are part of the App declaration. Letting a dashboard
    # editor replace them could turn a bounded preview into a partial KPI.
    if (
        data_source.get("rows_path") != contract.get("rows_path")
        or data_source.get("total_path") != contract.get("total_path")
        or dict(data_source.get("query_params") or {})
        != dict(contract.get("params") or {})
    ):
        return {"value": None, "error": "declared_query_contract_mismatch"}
    paths = _declared_query_output_paths(query, data_source)
    if paths is None:
        return {"value": None, "error": "declared_query_output_not_qualified"}

    base_params = dict(data_source.get("query_params") or {})
    try:
        invoked = await invoke_app_query(
            user_id=user_id,
            workspace_id=workspace_id,
            app_id=app_id,
            query_key=query_key,
            params=base_params,
        )
    except Exception:  # noqa: BLE001 — failures are visible, never zero
        _logger.exception(
            "Dashboard declared query failed (app_id=%s, query_key=%s)",
            app_id,
            query_key,
        )
        return {"value": None, "error": "declared_query_failed"}
    output = invoked.get("output") or {}
    rows = _json_path_value(output, paths[0])
    total = _json_path_value(output, paths[1])
    if (
        not isinstance(rows, list)
        or any(not isinstance(row, dict) for row in rows)
        or isinstance(total, bool)
        or not isinstance(total, int)
        or total < 0
        or len(rows) != total
    ):
        return {"value": None, "error": "incomplete_declared_query"}
    if total > int(data_source.get("budget", 5000)):
        return {
            "value": None,
            "error": "over_budget",
            "total": total,
            "budget": int(data_source.get("budget", 5000)),
        }

    aggregate_rows_input = [
        {
            "id": str(row.get("entry_id") or row.get("id") or f"row-{index}"),
            "title": str(row.get("title") or ""),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "custom_fields": dict(row),
        }
        for index, row in enumerate(rows)
    ]
    row_ids = [item["id"] for item in aggregate_rows_input]
    if len(row_ids) != len(set(row_ids)):
        return {"value": None, "error": "duplicate_declared_query_rows"}
    field = str(data_source.get("field") or "").strip()
    if field.startswith("custom_fields."):
        field = field[len("custom_fields.") :]
    group_by = str(data_source.get("group_by") or "").strip()
    if group_by.startswith("custom_fields."):
        group_by = group_by[len("custom_fields.") :]
    elif group_by.startswith("date:custom_fields."):
        group_by = "date:" + group_by[len("date:custom_fields.") :]
    spec = AggregateSpec(
        op=data_source.get("op") or "count",
        field=field,
        group_by=group_by,
        timezone=data_source.get("timezone") or "UTC",
        scale=data_source.get("scale"),
        budget=data_source.get("budget", 5000),
    )
    aggregate = aggregate_rows(aggregate_rows_input, spec)
    if aggregate.get("error"):
        return {"value": None, **aggregate}
    groups = aggregate.get("groups") or []
    return {
        **aggregate,
        "total_matched": total,
        "series": [
            {"label": group.get("key", ""), "value": group.get("value")}
            for group in groups
            if isinstance(group, dict)
        ],
        "drill_through_supported": False,
    }


async def resolve_widget_data(
    *,
    user_id: str,
    app_id: str,
    widget: Dict[str, Any],
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve payload data for a single dashboard widget."""
    app = await _get_app_or_none(app_id)
    ds = dict(widget.get("data_source") or {})
    ds.setdefault("kind", "count")
    if app is not None:
        from app.services.query_boundary import decide_app

        decision = decide_app(app)
        if not decision.allowed and not (
            decision.code == "app_domain" and ds.get("kind") == "declared_query"
        ):
            return {
                "value": None,
                "total_matched": 0,
                "entries": [],
                "metrics": [],
                "refused": decision.public(app_id=app_id),
            }
    wtype = str(widget.get("type") or "")
    if ds.get("kind") == "declared_query":
        return await _resolve_declared_query_aggregate(
            user_id=user_id,
            app_id=app_id,
            workspace_id=workspace_id,
            data_source=ds,
        )

    if ds.get("kind") == "aggregate":
        return await _resolve_aggregate_data_source(
            user_id=user_id,
            app_id=app_id,
            workspace_id=workspace_id,
            data_source=ds,
        )

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
                    **({"refused": row["refused"]} if row.get("refused") else {}),
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

    if wtype in ("recent_entries", "table_widget"):
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

    from app.services.query_boundary import decide_app

    app_query_only = decide_app(app).code == "app_domain"
    declared_queries: Dict[str, Dict[str, Any]] = {}
    if app_query_only:
        from app.services.app_queries.registry import list_registered_queries

        declared_queries = list_registered_queries(workspace_id or "", app_id)

    digest = (
        {"total_entries": 0}
        if app_query_only
        else await activity_digest(
            user_id=user_id,
            scope="app",
            scope_id=app_id,
            period="week",
            workspace_id=workspace_id,
        )
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
        rationale: str = "Suggested from the App's current structure.",
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
                "rationale": rationale,
            }
        )
        if x + int(default.get("w", w)) >= 12:
            y += int(default.get("h", h))

    total_entries = digest.get("total_entries", 0)
    # A starter dashboard should describe the user's operation, not Integral's
    # internals.  The first named tracks are the only universally available
    # domain signal, so make their record counts the headline metrics instead
    # of generic "entries" and platform lifecycle status.
    if app_query_only:
        # Generic Entry scans intentionally skip package-owned Tracks. Only
        # emit widgets for query contracts that explicitly describe a
        # complete, schema-typed row set and its total.
        for index, query in enumerate(
            sorted(declared_queries.values(), key=lambda row: str(row.get("key") or ""))
        ):
            dashboard = query.get("dashboard")
            if not isinstance(dashboard, dict):
                continue
            data_source = {
                "kind": "declared_query",
                "query_key": query.get("key"),
                "query_params": dict(dashboard.get("params") or {}),
                "rows_path": dashboard.get("rows_path"),
                "total_path": dashboard.get("total_path"),
                "budget": 5000,
                "op": "count",
            }
            if _declared_query_output_paths(query, data_source) is None:
                continue
            query_name = str(
                dashboard.get("title")
                or query.get("name")
                or query.get("key")
                or "App query"
            )
            _add(
                "metric_card",
                query_name,
                x=(index % 3) * 4,
                w=4,
                h=2,
                data_source=data_source,
                rationale=(
                    "Counts the complete rows returned by this App's explicitly "
                    "dashboard-enabled declared query."
                ),
            )
    else:
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

    # Suggest field-aware questions only when an attached Track schema supplies
    # the required type information. Generic fallback widgets above remain
    # useful for sparse or untyped Apps.
    recommendations = 0
    track_fields = (
        []
        if app_query_only
        else [(track, await _track_dashboard_fields(track)) for track in track_list]
    )
    candidates_by_track: List[List[Dict[str, Any]]] = []
    for track, fields in track_fields:
        # Configuration and reference tables (rates, settings, lookup tables)
        # describe policy; their values are not operational dashboard measures.
        if _is_configuration_track(track):
            candidates_by_track.append([])
            continue
        date_fields = [
            field
            for field in fields
            if field["type"] == "date" and _is_due_date_field(field)
        ]
        lifecycle_fields = [
            field
            for field in fields
            if field["type"] == "select"
            and (
                field["key"].casefold() in {"status", "state", "lifecycle"}
                or field["name"].casefold() in {"status", "state", "lifecycle"}
            )
        ]
        candidates: List[Dict[str, Any]] = []
        if date_fields and lifecycle_fields:
            due_field = date_fields[0]
            status_field = lifecycle_fields[0]
            now = datetime.now(timezone.utc)
            terminal = {
                str(value).strip()
                for value in status_field.get("enum", [])
                if str(value).strip().casefold()
                in {
                    "done",
                    "complete",
                    "completed",
                    "closed",
                    "paid",
                    "cancelled",
                    "canceled",
                    "resolved",
                }
            }
            overdue_filters: List[Dict[str, Any]] = [
                {
                    "field": f"custom_fields.{due_field['key']}",
                    "op": "lt",
                    "value": now.isoformat(),
                }
            ]
            if terminal:
                overdue_filters.append(
                    {
                        "field": f"custom_fields.{status_field['key']}",
                        "op": "not_in",
                        "value": sorted(terminal),
                    }
                )
            candidates.append(
                {
                    "widget_type": "metric_card",
                    "title": f"Overdue {track.title.lower()}",
                    "data_source": {
                        "kind": "aggregate",
                        "op": "count",
                        "track_id": track.id,
                        "filters": overdue_filters,
                    },
                    "rationale": (
                        f"Counts {track.title} records past {due_field['name'].lower()}"
                        + (" that are not in a completed status." if terminal else ".")
                    ),
                }
            )
        field_groups = [
            [field for field in fields if _is_dashboard_sum_candidate(field)],
            [
                field
                for field in fields
                if field["type"] in {"select", "multi_select", "relation"}
            ],
            [field for field in fields if field["type"] == "date"],
        ]
        # Interleave field families within each Track, then interleave Tracks
        # below so early, field-rich Tracks cannot exhaust the App budget.
        for index in range(max((len(group) for group in field_groups), default=0)):
            for group in field_groups:
                if index >= len(group):
                    continue
                field = group[index]
                key = field["key"]
                name = field["name"]
                field_type = field["type"]
                if field_type in {"number", "currency", "money", "duration"}:
                    candidate = {
                        "widget_type": "metric_card",
                        "title": f"Total {name}",
                        "data_source": {
                            "kind": "aggregate",
                            "op": "sum",
                            "field": key,
                            "track_id": track.id,
                        },
                        "rationale": f"Supports tracking the total {name.lower()} recorded in {track.title}.",
                    }
                elif field_type in {"select", "multi_select", "relation"}:
                    candidate = {
                        "widget_type": "chart_bar",
                        "title": f"{name} breakdown",
                        "data_source": {
                            "kind": "grouped_count",
                            "group_by": f"custom_fields.{key}",
                            "track_id": track.id,
                        },
                        "rationale": f"Shows how {track.title} records are distributed by {name.lower()}.",
                    }
                else:
                    candidate = {
                        "widget_type": "chart_line",
                        "title": f"Records by {name}",
                        "data_source": {
                            "kind": "aggregate",
                            "op": "count",
                            "group_by": f"date:{key}",
                            "track_id": track.id,
                        },
                        "rationale": f"Shows the record trend using the declared {name.lower()} date field.",
                    }
                candidates.append(candidate)
        candidates_by_track.append(candidates)

    # The App-level cap must be shared fairly across Tracks, not consumed by
    # whichever schema happens to appear first in the GraphContext listing.
    index = 0
    while recommendations < 6 and any(
        index < len(items) for items in candidates_by_track
    ):
        for candidates in candidates_by_track:
            if recommendations >= 6:
                break
            if index < len(candidates):
                candidate = candidates[index]
                _add(
                    candidate["widget_type"],
                    candidate["title"],
                    data_source=candidate["data_source"],
                    rationale=candidate["rationale"],
                )
                recommendations += 1
        index += 1

    # Attach the exact resolver output as a preview. Dashboard and preview use
    # one data-source contract, so suggestions cannot invent separate totals.
    for widget in widgets:
        widget["preview"] = await resolve_widget_data(
            user_id=user_id,
            app_id=app_id,
            workspace_id=workspace_id,
            widget=widget,
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
