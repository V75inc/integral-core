"""Unit tests for dashboard widget validation and normalization."""

from __future__ import annotations

import asyncio

import pytest

from app.services.dashboard_widget_validation import (
    normalize_widget_specs,
    validate_widget_specs,
)


def test_validate_widget_specs_rejects_unknown_type():
    """Unknown widget types produce a validation error naming the bad type."""
    raw = [{"id": "w1", "type": "not_a_real_widget", "title": "Bad"}]
    errors = validate_widget_specs(raw)
    assert len(errors) == 1
    assert "unknown type" in errors[0]
    assert "not_a_real_widget" in errors[0]


def test_validate_widget_specs_rejects_chart_line_with_categorical_group_by():
    """chart_line with a non-date group_by is reported as invalid."""
    raw = [
        {
            "id": "w1",
            "type": "chart_line",
            "title": "Trend",
            "data_source": {"group_by": "status"},
        }
    ]
    errors = validate_widget_specs(raw)
    assert len(errors) == 1
    assert "chart_line requires group_by 'date'" in errors[0]


def test_normalize_widget_specs_drops_unknown_types():
    """Normalization keeps valid widgets and reports dropped unknown types."""
    raw = [
        {"id": "w1", "type": "metric_card", "title": "OK"},
        {"id": "w2", "type": "bogus_type", "title": "Nope"},
    ]
    widgets, dropped = normalize_widget_specs(raw)
    assert len(widgets) == 1
    assert widgets[0]["type"] == "metric_card"
    assert len(dropped) == 1
    assert dropped[0]["reason"] == "unknown_widget_type"


def test_dashboard_persistence_rejects_instead_of_silently_dropping_widgets():
    """Authoring must fail atomically when a requested widget is unsupported."""
    from app.services.dashboard_service import _normalize_widgets

    with pytest.raises(ValueError, match="nothing was saved"):
        _normalize_widgets([{"id": "w1", "type": "not_a_real_widget", "title": "Bad"}])


def test_normalize_widget_specs_defaults_chart_line_group_by_to_date():
    """chart_line without group_by defaults to date during normalization."""
    raw = [
        {
            "id": "w1",
            "type": "chart_line",
            "title": "Over time",
            "data_source": {},
        }
    ]
    widgets, dropped = normalize_widget_specs(raw)
    assert dropped == []
    assert widgets[0]["data_source"]["group_by"] == "date"


def test_grouped_chart_supports_explicit_profile_field_paths():
    """Dashboard charts group the app's declared Status, not lifecycle status."""
    from app.services.dashboard_service import _group_entries

    groups = _group_entries(
        [
            {"status": "active", "custom_fields": {"status": "Available"}},
            {"status": "active", "custom_fields": {"status": "Maintenance"}},
            {"status": "active", "custom_fields": {"status": "Available"}},
        ],
        "custom_fields.status",
    )

    assert {group["key"]: group["count"] for group in groups} == {
        "Available": 2,
        "Maintenance": 1,
    }


def test_dashboard_profile_filters_are_exact_and_do_not_fall_back():
    """Missing profile values must not broaden a dashboard data source."""
    from app.services.dashboard_service import _apply_profile_filters

    rows = _apply_profile_filters(
        [
            {"title": "A", "custom_fields": {"rental_status": "Active"}},
            {"title": "B", "custom_fields": {"rental_status": "Completed"}},
            {"title": "C", "custom_fields": {}},
        ],
        {"custom_fields.rental_status": "Active"},
    )

    assert [row["title"] for row in rows] == ["A"]


def test_dashboard_filters_share_queryspec_operators_and_paths():
    """Dashboards and governed queries select the same qualified fields."""
    from app.services.dashboard_service import _apply_profile_filters

    rows = _apply_profile_filters(
        [
            {"title": "A", "custom_fields": {"mileage": 100, "tags": ["due"]}},
            {"title": "B", "custom_fields": {"mileage": 20, "tags": []}},
        ],
        [
            {"field": "custom_fields.mileage", "op": "gte", "value": 50},
            {"field": "custom_fields.tags", "op": "contains", "value": "due"},
        ],
    )

    assert [row["title"] for row in rows] == ["A"]


def test_dashboard_request_upgrades_legacy_filter_map_to_typed_contract():
    """A saved-dashboard compatible map has one unambiguous persisted form."""
    from app.schemas.dashboards import DataSourceSpec

    source = DataSourceSpec(filters={"custom_fields.status": ["Available"]})

    assert source.model_dump()["filters"] == [
        {"field": "custom_fields.status", "op": "in", "value": ["Available"]}
    ]


def test_aggregate_dashboard_source_uses_typed_w3_1_contract():
    from app.schemas.dashboards import DataSourceSpec

    source = DataSourceSpec(
        kind="aggregate", op="sum", field="daily_rate", group_by="custom_fields.state"
    )
    assert source.model_dump()["op"] == "sum"
    assert source.model_dump()["budget"] == 5000
    with pytest.raises(Exception):
        DataSourceSpec(kind="aggregate", budget=5001)


def test_aggregate_validation_requires_field_and_bounds_scan_budget():
    raw = [
        {
            "id": "missing-field",
            "type": "metric_card",
            "data_source": {"kind": "aggregate", "op": "sum"},
        },
        {
            "id": "too-large",
            "type": "metric_card",
            "data_source": {"kind": "aggregate", "budget": 5001},
        },
    ]
    errors = validate_widget_specs(raw)
    assert len(errors) == 2
    assert "requires a field" in errors[0]
    assert "budget" in errors[1]


def test_progress_widget_requires_aggregate_and_positive_target():
    assert validate_widget_specs(
        [
            {
                "id": "p",
                "type": "progress",
                "config": {"target": 0},
                "data_source": {"kind": "aggregate"},
            }
        ]
    )
    assert validate_widget_specs(
        [
            {
                "id": "p",
                "type": "progress",
                "config": {"target": 10},
                "data_source": {"kind": "count"},
            }
        ]
    )


def test_chart_line_accepts_aggregate_business_date_bucket():
    raw = [
        {
            "id": "trend",
            "type": "chart_line",
            "data_source": {
                "kind": "aggregate",
                "op": "sum",
                "field": "duration",
                "group_by": "date:custom_fields.started_at",
            },
        }
    ]
    assert validate_widget_specs(raw) == []


@pytest.mark.asyncio
async def test_resolve_widget_data_chart_line_forces_date_group_by(monkeypatch):
    """chart_line data resolution coerces group_by to date as a safety net."""
    from app.services import dashboard_service as ds

    captured: dict = {}

    async def fake_grouped(*, user_id, workspace_id, data_source, app_id=None):
        captured["group_by"] = data_source.get("group_by")
        return {"series": [], "group_by": data_source.get("group_by")}

    monkeypatch.setattr(ds, "_resolve_grouped_chart", fake_grouped)

    result = await ds.resolve_widget_data(
        user_id="u1",
        app_id="a1",
        widget={
            "type": "chart_line",
            "data_source": {"group_by": "status"},
        },
    )
    assert captured["group_by"] == "date"
    assert result.get("group_by") == "date"


@pytest.mark.asyncio
async def test_dashboard_suggestion_leads_with_named_operating_areas(monkeypatch):
    """A generic dashboard should still speak the app's domain vocabulary."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.services import dashboard_service as ds

    app = SimpleNamespace(
        id="app-1",
        name="Car Rental Manager",
        nodes=AsyncMock(
            return_value=[
                SimpleNamespace(id="cars", title="Cars"),
                SimpleNamespace(id="customers", title="Customers"),
                SimpleNamespace(id="rentals", title="Rentals"),
            ]
        ),
    )

    async def visible(*_args, **_kwargs):
        return True

    async def digest(**_kwargs):
        return {"total_entries": 12}

    async def preview(**_kwargs):
        return {"value": 12, "total_matched": 12}

    monkeypatch.setattr(ds, "can_view_app", visible)
    monkeypatch.setattr(ds, "_get_app_or_none", AsyncMock(return_value=app))
    monkeypatch.setattr(ds, "activity_digest", digest)
    monkeypatch.setattr(ds, "resolve_widget_data", preview)

    suggestion = await ds.suggest_dashboard_template(user_id="u1", app_id="app-1")

    titles = [widget["title"] for widget in suggestion["widgets"]]
    assert titles[:3] == ["Cars records", "Customers records", "Rentals records"]
    assert "Status breakdown" not in titles
    assert all(
        "rationale" in widget and "preview" in widget
        for widget in suggestion["widgets"]
    )


@pytest.mark.asyncio
async def test_dashboard_suggestion_uses_schema_and_exact_preview(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.services import dashboard_service as ds

    track = SimpleNamespace(id="track-1", title="Invoices")
    app = SimpleNamespace(
        id="app-1",
        name="Billing",
        nodes=AsyncMock(return_value=[track]),
    )

    async def visible(*_args, **_kwargs):
        return True

    async def digest(**_kwargs):
        return {"total_entries": 4}

    async def fields(_track):
        return [
            {"key": "amount", "name": "Amount", "type": "currency"},
            {"key": "issued_at", "name": "Issued at", "type": "date"},
            {
                "key": "status",
                "name": "Status",
                "type": "select",
                "enum": ["open", "paid"],
            },
        ]

    async def preview(**kwargs):
        widget = kwargs["widget"]
        source = widget["data_source"]
        return {"value": "400" if source.get("field") == "amount" else 4}

    monkeypatch.setattr(ds, "can_view_app", visible)
    monkeypatch.setattr(ds, "_get_app_or_none", AsyncMock(return_value=app))
    monkeypatch.setattr(ds, "activity_digest", digest)
    monkeypatch.setattr(ds, "_track_dashboard_fields", fields)
    monkeypatch.setattr(ds, "resolve_widget_data", preview)

    suggestion = await ds.suggest_dashboard_template(user_id="u1", app_id="app-1")
    by_title = {widget["title"]: widget for widget in suggestion["widgets"]}

    assert by_title["Total Amount"]["data_source"]["op"] == "sum"
    assert by_title["Total Amount"]["preview"] == {"value": "400"}
    assert (
        by_title["Records by Issued at"]["data_source"]["group_by"] == "date:issued_at"
    )
    assert "rationale" in by_title["Records by Issued at"]
    overdue = by_title["Overdue invoices"]["data_source"]
    assert overdue["op"] == "count"
    assert overdue["filters"][0]["field"] == "custom_fields.issued_at"
    assert overdue["filters"][1] == {
        "field": "custom_fields.status",
        "op": "not_in",
        "value": ["paid"],
    }


@pytest.mark.asyncio
async def test_query_all_entries_walks_every_page_without_a_hidden_cap(monkeypatch):
    """Dashboard aggregations must see records beyond an arbitrary first page."""
    from app.services import agent_insights

    calls: list[int] = []

    async def fake_query_entries(*, limit: int, offset: int, **_kwargs):
        calls.append(offset)
        all_rows = [{"id": str(i)} for i in range(1_001)]
        return {
            "entries": all_rows[offset : offset + limit],
            "total": len(all_rows),
            "filters_applied": {"workspace_id": "ws-1"},
        }

    monkeypatch.setattr(agent_insights, "query_entries", fake_query_entries)
    result = await agent_insights.query_all_entries(
        user_id="u-1", workspace_id="ws-1", page_size=500
    )

    assert calls == [0, 500, 1000]
    assert result["total"] == 1_001
    assert len(result["entries"]) == 1_001
    assert result["complete"] is True


@pytest.mark.asyncio
async def test_aggregate_dashboard_matches_shared_engine_and_emits_chart_series(
    monkeypatch,
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.services import dashboard_service as ds

    app = SimpleNamespace(id="app-1")
    monkeypatch.setattr(ds, "_get_app_or_none", AsyncMock(return_value=app))
    monkeypatch.setattr(
        ds, "_data_source_track_ids", AsyncMock(return_value=["t1", "t2"])
    )
    calls = []

    async def query(*, track_id, **kwargs):
        calls.append((track_id, kwargs.get("workspace_id")))
        amount = 120 if track_id == "t1" else 80
        return {
            "entries": [
                {
                    "id": track_id,
                    "custom_fields": {
                        "revenue": {"amount": amount, "currency": "GYD"},
                        "state": "won",
                    },
                }
            ],
            "total": 1,
            "complete": True,
        }

    monkeypatch.setattr(ds, "query_all_entries", query)
    result = await ds.resolve_widget_data(
        user_id="u1",
        app_id="app-1",
        workspace_id="ws1",
        widget={
            "type": "metric_card",
            "data_source": {
                "kind": "aggregate",
                "op": "sum",
                "field": "revenue",
                "group_by": "state",
            },
        },
    )

    assert calls == [("t1", "ws1"), ("t2", "ws1")]
    assert result["value"] == "200"
    assert result["display"] == "200"
    assert result["currency"] == "GYD"
    assert result["series"] == [{"label": "won", "value": "200"}]
    assert result["total_matched"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query_result, expected",
    [
        ({"entries": [{"id": "1"}], "complete": False}, "incomplete_query"),
        ({"error": "backend_unavailable"}, "backend_unavailable"),
        ({"refused": {"code": "app_domain"}}, "query_refused"),
    ],
)
async def test_aggregate_dashboard_never_returns_partial_or_refused_value(
    monkeypatch, query_result, expected
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.services import dashboard_service as ds

    monkeypatch.setattr(
        ds, "_get_app_or_none", AsyncMock(return_value=SimpleNamespace(id="app-1"))
    )
    monkeypatch.setattr(ds, "_data_source_track_ids", AsyncMock(return_value=["t1"]))
    monkeypatch.setattr(ds, "query_all_entries", AsyncMock(return_value=query_result))

    result = await ds.resolve_widget_data(
        user_id="u1",
        app_id="app-1",
        widget={
            "type": "metric_card",
            "data_source": {"kind": "aggregate", "op": "count"},
        },
    )

    assert result["value"] is None
    assert result["error"] == expected


@pytest.mark.asyncio
async def test_aggregate_dashboard_preserves_shared_engine_budget_refusal(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.services import dashboard_service as ds

    monkeypatch.setattr(
        ds, "_get_app_or_none", AsyncMock(return_value=SimpleNamespace(id="app-1"))
    )
    monkeypatch.setattr(ds, "_data_source_track_ids", AsyncMock(return_value=["t1"]))
    monkeypatch.setattr(
        ds,
        "query_all_entries",
        AsyncMock(
            return_value={
                "entries": [{"id": str(i)} for i in range(2)],
                "complete": True,
            }
        ),
    )
    result = await ds.resolve_widget_data(
        user_id="u1",
        app_id="app-1",
        widget={
            "type": "metric_card",
            "data_source": {"kind": "aggregate", "op": "count", "budget": 1},
        },
    )

    assert result["value"] is None
    assert result["error"] == "over_budget"


@pytest.mark.asyncio
async def test_table_widget_uses_recent_entries_source(monkeypatch):
    from app.services import dashboard_service as ds

    async def collect(**kwargs):
        assert kwargs["data_source"]["limit"] == 3
        return ([{"id": "e1", "title": "Record one"}], 1)

    monkeypatch.setattr(ds, "_collect_data_source_entries", collect)
    result = await ds.resolve_widget_data(
        user_id="u1",
        app_id="a1",
        widget={"type": "table_widget", "data_source": {"limit": 3}},
    )

    assert result["entries"] == [{"id": "e1", "title": "Record one"}]


@pytest.mark.asyncio
async def test_dashboard_drilldown_compiles_exact_group_and_filter_scope(monkeypatch):
    from app.services import dashboard_service as ds

    async def tracks(_app, _source):
        return ["track-a", "track-b"]

    monkeypatch.setattr(ds, "_data_source_track_ids", tracks)
    spec = await ds.build_dashboard_drilldown_spec(
        app=object(),
        widget={
            "data_source": {
                "kind": "aggregate",
                "op": "sum",
                "field": "amount",
                "group_by": "custom_fields.status",
                "track_ids": ["track-a", "track-b"],
                "filters": [
                    {"field": "custom_fields.region", "op": "eq", "value": "north"}
                ],
            }
        },
        group_key="open",
        cursor=None,
    )

    filters = [item.model_dump() for item in spec.filters]
    assert filters[0] == {
        "field": "track_id",
        "op": "in",
        "value": ["track-a", "track-b"],
    }
    assert {item["field"]: item["value"] for item in filters[1:]} == {
        "custom_fields.region": "north",
        "custom_fields.status": "open",
    }
    assert "custom_fields.amount" in spec.select


@pytest.mark.asyncio
async def test_dashboard_drilldown_business_date_bucket_uses_timezone_bounds(
    monkeypatch,
):
    from app.services import dashboard_service as ds

    monkeypatch.setattr(
        ds,
        "_data_source_track_ids",
        lambda _app, _source: asyncio.sleep(0, result=["track-a"]),
    )
    spec = await ds.build_dashboard_drilldown_spec(
        app=object(),
        widget={
            "data_source": {"group_by": "date:due_at", "timezone": "America/Guyana"}
        },
        group_key="2026-09-28",
        cursor=None,
    )
    bounds = [item for item in spec.filters if item.field == "custom_fields.due_at"]
    assert [item.op for item in bounds] == ["gte", "lt"]
    assert bounds[0].value.startswith("2026-09-28T04:00:00")
    assert bounds[1].value.startswith("2026-09-29T04:00:00")


@pytest.mark.asyncio
async def test_activity_digest_scans_every_accessible_track(monkeypatch):
    """A digest must not silently omit tracks after an arbitrary first page."""
    from types import SimpleNamespace

    from app.services import agent_insights

    tracks = [SimpleNamespace(id=f"n.Track.{idx}", title=str(idx)) for idx in range(26)]

    async def fake_tracks(_user_id):
        return tracks

    async def fake_entries(_user_id, _track_id, **_kwargs):
        return []

    monkeypatch.setattr(
        "app.services.permissions.get_user_accessible_tracks", fake_tracks
    )
    monkeypatch.setattr(
        "app.services.permissions.get_user_accessible_entries", fake_entries
    )

    result = await agent_insights.activity_digest(user_id="u1", period="week")

    assert result["total_tracks"] == 26
    assert len(result["track_summaries"]) == 26


@pytest.mark.asyncio
async def test_dashboard_track_ids_are_honored_instead_of_broadening_to_the_app(
    monkeypatch,
):
    """A multi-track dashboard source selects exactly its declared tracks."""
    from app.services import dashboard_service as ds

    calls: list[str] = []

    async def fake_track_ids(_app):
        return ["track-a", "track-b"]

    async def fake_app(_app_id):
        return object()

    async def fake_query_all_entries(*, track_id, **_kwargs):
        calls.append(track_id)
        return {
            "entries": [{"id": track_id, "custom_fields": {"state": "open"}}],
            "total": 1,
        }

    monkeypatch.setattr(ds, "_app_track_ids", fake_track_ids)
    monkeypatch.setattr(ds, "_get_app_or_none", fake_app)
    monkeypatch.setattr(ds, "query_all_entries", fake_query_all_entries)

    rows, total = await ds._collect_app_entries(
        user_id="u1",
        app_id="app-1",
        workspace_id="ws-1",
        data_source={"track_ids": ["track-b"]},
    )

    assert calls == ["track-b"]
    assert total == 1
    assert [row["id"] for row in rows] == ["track-b"]


@pytest.mark.asyncio
async def test_dashboard_rejects_track_outside_its_app(monkeypatch):
    """Invalid saved config must be visible as an error, never silently ignored."""
    from app.services import dashboard_service as ds

    async def fake_track_ids(_app):
        return ["track-a"]

    monkeypatch.setattr(ds, "_app_track_ids", fake_track_ids)

    with pytest.raises(ValueError, match="outside its app"):
        await ds._data_source_track_ids(object(), {"track_ids": ["track-b"]})


@pytest.mark.asyncio
async def test_activity_digest_honors_declared_multi_track_scope(monkeypatch):
    """Activity widgets must not ignore a selected subset of App tracks."""
    from app.services import dashboard_service as ds

    calls: list[tuple[str, str]] = []

    async def fake_app(_app_id):
        return object()

    async def fake_track_ids(_app, _source):
        return ["track-a", "track-b"]

    async def fake_digest(*, scope, scope_id, **_kwargs):
        calls.append((scope, scope_id))
        return {
            "since": "2026-09-01T00:00:00+00:00",
            "total_tracks": 1,
            "total_entries": 2,
            "recent_entry_count": 1,
            "track_summaries": [{"track_id": scope_id, "entry_count": 2}],
        }

    monkeypatch.setattr(ds, "_get_app_or_none", fake_app)
    monkeypatch.setattr(ds, "_data_source_track_ids", fake_track_ids)
    monkeypatch.setattr(ds, "activity_digest", fake_digest)

    result = await ds.resolve_widget_data(
        user_id="u1",
        app_id="app-1",
        widget={
            "type": "activity_digest",
            "data_source": {"track_ids": ["track-a", "track-b"], "period": "week"},
        },
        workspace_id="ws-1",
    )

    assert calls == [("track", "track-a"), ("track", "track-b")]
    assert result["total_tracks"] == 2
    assert result["total_entries"] == 4
    assert [row["track_id"] for row in result["tracks"]] == ["track-a", "track-b"]
