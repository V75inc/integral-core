"""Tests for dashboard staging bindings."""

from __future__ import annotations

import pytest

from app.agentive.tooling.bindings import _stage_create_dashboard


@pytest.mark.asyncio
async def test_stage_create_dashboard_raises_on_unknown_widget_type():
    """Staging create raises a helpful error for an unknown widget type."""
    with pytest.raises(ValueError, match="unknown type 'bogus_widget'"):
        await _stage_create_dashboard(
            {
                "app_id": "app-1",
                "name": "Test board",
                "widgets": [
                    {
                        "id": "w1",
                        "type": "bogus_widget",
                        "title": "Bad",
                    }
                ],
            }
        )


@pytest.mark.asyncio
async def test_stage_create_dashboard_counts_valid_widgets_only():
    """The staging card reports the count of valid, persisted widgets."""
    staged = await _stage_create_dashboard(
        {
            "app_id": "app-1",
            "name": "Overview",
            "widgets": [
                {
                    "id": "w1",
                    "type": "metric_card",
                    "title": "Total",
                    "grid": {"x": 0, "y": 0, "w": 3, "h": 2},
                },
                {
                    "id": "w2",
                    "type": "chart_bar",
                    "title": "By status",
                    "grid": {"x": 3, "y": 0, "w": 6, "h": 4},
                    "data_source": {"group_by": "status"},
                },
            ],
        }
    )
    assert "2 widget(s)" in staged["diff_human"]
    assert len(staged["payload"]["widgets"]) == 2
