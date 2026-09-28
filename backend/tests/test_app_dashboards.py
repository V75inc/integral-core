"""Tests for app dashboard substrate and widget registry."""

from __future__ import annotations

import json

import pytest

from app.api import apps_dashboards
from app.models.edges import CONTAINS
from app.models.nodes import Entry, Track
from app.views import dashboard_widget_types as dwt


def test_dashboard_widget_registry_has_chart_types():
    keys = set(dwt.allowed_keys())
    assert "metric_card" in keys
    assert "chart_bar" in keys
    assert "chart_line" in keys
    assert "chart_pie" in keys
    assert "activity_digest" in keys
    assert "table_widget" in keys
    assert "progress" in keys
    assert (
        "aggregate"
        in dwt.get_spec("metric_card").data_source_schema["properties"]["kind"]["enum"]
    )


def test_validate_widget_type():
    assert dwt.validate_widget_type("chart_bar") is True
    assert dwt.validate_widget_type("not_a_widget") is False


def test_compact_single_column_widget_layout():
    from app.services.dashboard_service import _compact_widget_layout

    widgets = [
        {
            "id": "a",
            "grid": {"x": 0, "y": 0, "w": 12, "h": 2},
        },
        {
            "id": "b",
            "grid": {"x": 0, "y": 2, "w": 6, "h": 4},
        },
        {
            "id": "c",
            "grid": {"x": 0, "y": 6, "w": 6, "h": 4},
        },
    ]
    packed = _compact_widget_layout(widgets, columns=12)
    assert packed[1]["grid"]["x"] == 0
    assert packed[2]["grid"]["x"] == 6
    assert packed[1]["grid"]["y"] == packed[2]["grid"]["y"]


@pytest.mark.asyncio
async def test_create_dashboard_wires_graph(authenticated_client, test_user):
    """Dashboard create attaches to App via Dashboards registry (I-GRAPH-01)."""
    from app.models.nodes import App, Dashboard, Dashboards
    from app.services.app_graph import get_or_create_dashboards_registry

    app_resp = await authenticated_client.post(
        "/api/apps",
        json={"name": "Dash Test App", "visibility": "private"},
    )
    assert app_resp.status_code == 200, app_resp.text
    app_id = app_resp.json()["app"]["id"]

    create_resp = await authenticated_client.post(
        f"/api/apps/{app_id}/dashboards",
        json={
            "name": "Overview",
            "widgets": [
                {
                    "id": "w1",
                    "type": "metric_card",
                    "title": "Total",
                    "grid": {"x": 0, "y": 0, "w": 3, "h": 2},
                    "config": {},
                    "data_source": {"kind": "count"},
                }
            ],
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    dash_id = create_resp.json()["id"]

    app = await App.get(app_id)
    assert app is not None
    dreg = await get_or_create_dashboards_registry(app)
    assert isinstance(dreg, Dashboards)

    dash = await Dashboard.get(dash_id)
    assert dash is not None
    assert dash.app_id == app_id
    assert len(dash.widgets) == 1
    assert dash.widgets[0]["type"] == "metric_card"

    list_resp = await authenticated_client.get(f"/api/apps/{app_id}/dashboards")
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 1

    # Registry lookup must be idempotent (CONTAINS, not base Edge).
    dreg_again = await get_or_create_dashboards_registry(app)
    assert dreg_again.id == dreg.id
    list_again = await authenticated_client.get(f"/api/apps/{app_id}/dashboards")
    assert list_again.status_code == 200
    assert list_again.json()["total"] == 1

    substrate_resp = await authenticated_client.get("/api/dashboard-widget-substrate")
    assert substrate_resp.status_code == 200
    assert len(substrate_resp.json()["widget_types"]) >= 8


@pytest.mark.asyncio
async def test_suggest_dashboard(authenticated_client):
    app_resp = await authenticated_client.post(
        "/api/apps",
        json={"name": "Suggest App", "visibility": "private"},
    )
    assert app_resp.status_code == 200, app_resp.text
    app_id = app_resp.json()["app"]["id"]
    suggest_resp = await authenticated_client.get(
        f"/api/apps/{app_id}/dashboards/suggest"
    )
    assert suggest_resp.status_code == 200
    body = suggest_resp.json()
    assert body.get("widgets")
    assert body.get("name")


@pytest.mark.asyncio
async def test_dashboard_drillthrough_returns_fixed_governed_membership(
    authenticated_client,
):
    app_resp = await authenticated_client.post(
        "/api/apps", json={"name": "Drillthrough App", "visibility": "private"}
    )
    assert app_resp.status_code == 200, app_resp.text
    app_id = app_resp.json()["app"]["id"]
    track_resp = await authenticated_client.post(
        "/api/tracks", json={"title": "Invoices", "app_id": app_id}
    )
    assert track_resp.status_code == 200, track_resp.text
    track_id = track_resp.json()["track"]["id"]
    entry_resp = await authenticated_client.post(
        "/api/entries",
        json={
            "track_id": track_id,
            "title": "Invoice A",
        },
    )
    assert entry_resp.status_code == 200, entry_resp.text
    entry_id = entry_resp.json()["entry"]["id"]
    dash_resp = await authenticated_client.post(
        f"/api/apps/{app_id}/dashboards",
        json={
            "name": "Revenue",
            "widgets": [
                {
                    "id": "revenue",
                    "type": "metric_card",
                    "title": "Revenue",
                    "data_source": {
                        "kind": "aggregate",
                        "op": "count",
                        "track_id": track_id,
                    },
                },
                {
                    "id": "status",
                    "type": "chart_bar",
                    "title": "By status",
                    "data_source": {"track_id": track_id, "group_by": "status"},
                },
            ],
        },
    )
    assert dash_resp.status_code == 200, dash_resp.text
    dashboard_id = dash_resp.json()["id"]

    first = await authenticated_client.post(
        f"/api/apps/{app_id}/dashboards/{dashboard_id}/drill-through",
        json={"widget_id": "revenue"},
    )
    assert first.status_code == 200, first.text
    first_payload = first.json()
    assert first_payload["result_set_id"]
    assert first_payload["membership_limit"] <= 100
    assert [row["id"] for row in first_payload["items"]] == [entry_id]
    assert first_payload["calculation"] == {
        "op": "count",
        "field": None,
        "group_by": None,
    }
    assert first_payload["page_calculation"]["value"] == 1
    assert first_payload["total_estimate"] == 1
    assert first_payload["membership_complete"] is True
    assert (
        first_payload["continuation_contract"]
        == "cursor_pages_revalidated_under_current_access"
    )
    assert first_payload["current_widget_value"] == 1, first_payload
    assert first_payload["page_truncated"] is False
    assert first_payload["track_navigation"] == {
        "track_id": track_id,
        "filters": [],
    }

    grouped = await authenticated_client.post(
        f"/api/apps/{app_id}/dashboards/{dashboard_id}/drill-through",
        json={"widget_id": "status", "group_key": "active"},
    )
    assert grouped.status_code == 200, grouped.text
    grouped_payload = grouped.json()
    assert grouped_payload["current_widget_value"] == 1, grouped_payload
    assert grouped_payload["page_calculation"]["value"] == 1
    assert grouped_payload["track_navigation"] == {
        "track_id": track_id,
        "filters": [{"field": "status", "op": "eq", "value": "active"}],
    }
    filtered_track = await authenticated_client.get(
        f"/api/tracks/{track_id}/entries",
        params={
            "dashboard_filters": json.dumps(
                grouped_payload["track_navigation"]["filters"]
            )
        },
    )
    assert filtered_track.status_code == 200, filtered_track.text
    assert [row["id"] for row in filtered_track.json()["entries"]] == [entry_id]

    update = await authenticated_client.put(
        f"/api/entries/{entry_id}", json={"title": "Invoice A revised"}
    )
    assert update.status_code == 200, update.text
    follow_up = await authenticated_client.post(
        f"/api/apps/{app_id}/dashboards/{dashboard_id}/drill-through",
        json={"widget_id": "revenue", "result_set_id": first_payload["result_set_id"]},
    )
    assert follow_up.status_code == 200, follow_up.text
    assert [row["id"] for row in follow_up.json()["items"]] == [entry_id]
    assert follow_up.json()["items"][0]["title"] == "Invoice A revised"


@pytest.mark.asyncio
async def test_dashboard_continuation_keeps_initial_membership_after_new_entry(
    authenticated_client, monkeypatch
):
    app_resp = await authenticated_client.post(
        "/api/apps", json={"name": "Paged Dashboard", "visibility": "private"}
    )
    assert app_resp.status_code == 200, app_resp.text
    app_id = app_resp.json()["app"]["id"]
    track_resp = await authenticated_client.post(
        "/api/tracks", json={"title": "Paged records", "app_id": app_id}
    )
    assert track_resp.status_code == 200, track_resp.text
    track_id = track_resp.json()["track"]["id"]
    track = await Track.get(track_id)
    assert track is not None
    for index in range(3):
        entry = await Entry.create(
            title=f"Record {index}",
            track_id=track_id,
            owner_id=track.owner_id,
            workspace_id=track.workspace_id,
        )
        await track.connect(entry, edge=CONTAINS)
    dash_resp = await authenticated_client.post(
        f"/api/apps/{app_id}/dashboards",
        json={
            "name": "Paged count",
            "widgets": [
                {
                    "id": "count",
                    "type": "metric_card",
                    "title": "Record count",
                    "data_source": {
                        "kind": "aggregate",
                        "op": "count",
                        "track_id": track_id,
                    },
                }
            ],
        },
    )
    assert dash_resp.status_code == 200, dash_resp.text
    dashboard_id = dash_resp.json()["id"]
    original_builder = apps_dashboards.build_dashboard_drilldown_spec

    async def two_entry_pages(**kwargs):
        spec = await original_builder(**kwargs)
        return spec.model_copy(update={"limit": 2})

    monkeypatch.setattr(
        apps_dashboards, "build_dashboard_drilldown_spec", two_entry_pages
    )
    first = await authenticated_client.post(
        f"/api/apps/{app_id}/dashboards/{dashboard_id}/drill-through",
        json={"widget_id": "count"},
    )
    assert first.status_code == 200, first.text
    first_page = first.json()
    assert len(first_page["items"]) == 2
    assert first_page["total_estimate"] == 3
    assert first_page["next_cursor"]

    late = await Entry.create(
        title="Added while paging",
        track_id=track_id,
        owner_id=track.owner_id,
        workspace_id=track.workspace_id,
    )
    await track.connect(late, edge=CONTAINS)
    second = await authenticated_client.post(
        f"/api/apps/{app_id}/dashboards/{dashboard_id}/drill-through",
        json={
            "widget_id": "count",
            "cursor": first_page["next_cursor"],
        },
    )
    assert second.status_code == 200, second.text
    second_page = second.json()
    assert len(second_page["items"]) <= 2
    assert second_page["total_estimate"] == 4
    assert second_page["current_widget_value"] == 4
