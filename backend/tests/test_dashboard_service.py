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
