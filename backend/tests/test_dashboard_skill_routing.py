"""Host keeps the dashboard skill in view; the model decides if it applies."""

from __future__ import annotations

from types import SimpleNamespace

from app.api.ai_chat import (
    _DASHBOARD_SKILL_DIRECTIVE,
    _focused_dashboard_id,
    _is_dashboard_skill_request,
)


def test_focused_dashboard_id_reads_page_metadata() -> None:
    ctx = SimpleNamespace(metadata={"focused_dashboard_id": "n.Dashboard.abc"})
    assert _focused_dashboard_id(ctx) == "n.Dashboard.abc"
    assert _focused_dashboard_id(None) is None
    assert _focused_dashboard_id(SimpleNamespace(metadata=None)) is None


def test_dashboard_note_is_limited_to_dashboard_actions_or_focused_dashboard() -> None:
    assert _is_dashboard_skill_request("agrega un gráfico", None) is True
    assert (
        _is_dashboard_skill_request("Please add a chart showing monthly revenue", None)
        is True
    )
    assert _is_dashboard_skill_request("hello", None) is False
    assert _is_dashboard_skill_request("What is a dashboard?", None) is False
    assert (
        _is_dashboard_skill_request(
            "Please summarize this page",
            SimpleNamespace(metadata={"focused_dashboard_id": "n.Dashboard.abc"}),
        )
        is True
    )
    assert _is_dashboard_skill_request("   ", None) is False
    assert "Ignore this note unless" in _DASHBOARD_SKILL_DIRECTIVE
    assert "whatever language" in _DASHBOARD_SKILL_DIRECTIVE
