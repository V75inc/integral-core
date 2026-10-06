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


@pytest.mark.asyncio
async def test_stage_create_dashboard_normalizes_common_semantic_widget_names():
    """Natural-language dashboard labels map to supported renderer widgets."""
    staged = await _stage_create_dashboard(
        {
            "app_id": "app-1",
            "name": "Operations",
            "widgets": [
                {"id": "kpi", "type": "kpi", "title": "Open requests"},
                {"id": "chart", "type": "chart", "title": "By status"},
                {"id": "feed", "type": "feed", "title": "Recent activity"},
            ],
        }
    )

    assert [widget["type"] for widget in staged["payload"]["widgets"]] == [
        "metric_card",
        "chart_bar",
        "activity_digest",
    ]
    assert "normalized 3 widget type(s)" in staged["diff_human"]


@pytest.mark.asyncio
async def test_stage_create_dashboard_auto_fills_when_widgets_omitted():
    """Name-only create must not stage an empty board."""
    staged = await _stage_create_dashboard(
        {
            "app_id": "app-1",
            "name": "Overview",
        }
    )
    assert len(staged["payload"]["widgets"]) >= 2
    assert "auto-filled" in staged["diff_human"]


@pytest.mark.asyncio
async def test_dashboard_rejects_invalid_execution_shape_before_approval():
    from pydantic import ValidationError

    from app.agentive.tooling.bindings import _stage_update_dashboard

    widget = {"id": "count", "type": "metric_card", "data_source": {"field": None}}
    with pytest.raises(ValidationError):
        await _stage_create_dashboard(
            {"app_id": "app-1", "name": "Overview", "widgets": [widget]}
        )
    with pytest.raises(ValidationError):
        await _stage_update_dashboard(
            {"app_id": "app-1", "dashboard_id": "d-1", "widgets": [widget]}
        )


@pytest.mark.asyncio
async def test_dashboard_rejects_columns_the_renderer_cannot_display():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="Extra inputs"):
        await _stage_create_dashboard(
            {
                "app_id": "app-1",
                "name": "Upcoming",
                "widgets": [
                    {
                        "id": "jobs",
                        "type": "table_widget",
                        "config": {"columns": ["due_date", "assignee"]},
                    }
                ],
            }
        )


@pytest.mark.asyncio
async def test_dashboard_business_fields_require_visible_published_schema(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.services import dashboard_service as service
    from app.services import permissions

    monkeypatch.setattr(
        service, "_get_app_or_none", AsyncMock(return_value=SimpleNamespace(id="app-1"))
    )
    monkeypatch.setattr(service, "can_view_app", AsyncMock(return_value=True))
    monkeypatch.setattr(
        service, "_data_source_track_ids", AsyncMock(return_value=["track-1"])
    )
    monkeypatch.setattr(
        service.Track, "get", AsyncMock(return_value=SimpleNamespace(id="track-1"))
    )
    monkeypatch.setattr(permissions, "can_view_track", AsyncMock(return_value=True))
    monkeypatch.setattr(
        service, "_track_dashboard_fields", AsyncMock(return_value=[{"key": "status"}])
    )
    valid = [{"data_source": {"group_by": "custom_fields.status"}}]
    await service.validate_dashboard_field_bindings(
        user_id="u1", app_id="app-1", widgets=valid
    )
    with pytest.raises(ValueError, match="category"):
        await service.validate_dashboard_field_bindings(
            user_id="u1",
            app_id="app-1",
            widgets=[{"data_source": {"group_by": "custom_fields.category"}}],
        )
    monkeypatch.setattr(permissions, "can_view_track", AsyncMock(return_value=False))
    with pytest.raises(PermissionError):
        await service.validate_dashboard_field_bindings(
            user_id="u1", app_id="app-1", widgets=valid
        )
