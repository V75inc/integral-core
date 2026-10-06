"""Invented dashboard widget types must not fail an app build."""

import pytest

from app.agentive.tooling.bindings import (
    _stage_create_dashboard,
    prepare_dashboard_widgets,
)


def test_summary_tile_and_table_become_renderer_types():
    widgets, notes = prepare_dashboard_widgets(
        [
            {
                "name": "All Products",
                "type": "table",
                "track_id": "{{track.id:Products}}",
            },
            {
                "name": "Total Products",
                "type": "summary_tile",
                "aggregation": "count",
            },
            {"name": "Total Stock", "type": "summary_tiles", "aggregation": "sum"},
        ]
    )
    assert [widget["type"] for widget in widgets] == [
        "recent_entries",
        "metric_card",
        "metric_card",
    ]
    assert notes == ["normalized 3 widget type(s)"]


def test_unknown_type_is_dropped_and_a_blank_slate_uses_the_starter():
    mixed, notes = prepare_dashboard_widgets(
        [
            {"type": "metric_card", "title": "Kept"},
            {"type": "flux_capacitor", "title": "Dropped"},
        ]
    )
    assert [widget["type"] for widget in mixed] == ["metric_card"]
    assert "flux_capacitor" in notes[0]

    starter, starter_notes = prepare_dashboard_widgets(
        [{"type": "not_a_widget"}, {"type": "also_nope"}]
    )
    assert starter_notes[0].startswith("replaced unrecognized widgets")
    assert {widget["type"] for widget in starter} <= {
        "metric_card",
        "track_breakdown",
        "activity_digest",
        "chart_bar",
        "chart_line",
        "chart_pie",
        "metric_row",
        "recent_entries",
    }


def test_invented_kpi_and_chart_names_resolve_without_a_new_alias():
    widgets, _notes = prepare_dashboard_widgets(
        [
            {"type": "kpi_card", "title": "Open"},
            {"type": "bar_chart", "title": "By status"},
        ]
    )
    assert [widget["type"] for widget in widgets] == ["metric_card", "chart_bar"]


@pytest.mark.asyncio
async def test_create_dashboard_stages_the_inventory_board():
    staged = await _stage_create_dashboard(
        {
            "app_id": "{{app.id}}",
            "name": "Inventory Dashboard",
            "widgets": [
                {"name": "All Products", "type": "table"},
                {"name": "Total Products", "type": "summary_tile"},
                {"name": "Total Stock", "type": "summary_tile"},
            ],
        }
    )
    assert staged["kind"] == "create_dashboard"
    assert [widget["type"] for widget in staged["payload"]["widgets"]] == [
        "recent_entries",
        "metric_card",
        "metric_card",
    ]
