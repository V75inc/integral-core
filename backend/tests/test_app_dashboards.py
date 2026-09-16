"""Tests for app dashboard substrate and widget registry."""

from __future__ import annotations

import pytest

from app.views import dashboard_widget_types as dwt


def test_dashboard_widget_registry_has_chart_types():
    keys = set(dwt.allowed_keys())
    assert "metric_card" in keys
    assert "chart_bar" in keys
    assert "chart_line" in keys
    assert "chart_pie" in keys
    assert "activity_digest" in keys


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
