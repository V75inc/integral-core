"""Unit tests for dashboard widget validation and normalization."""

from __future__ import annotations

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
