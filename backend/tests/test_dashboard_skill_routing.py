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


def test_dashboard_note_is_shown_for_any_message_and_the_model_decides() -> None:
    assert _is_dashboard_skill_request("agrega un gráfico", None) is True
    assert _is_dashboard_skill_request("hello", None) is True
    assert _is_dashboard_skill_request("   ", None) is False
    assert "Ignore this note unless" in _DASHBOARD_SKILL_DIRECTIVE
    assert "whatever language" in _DASHBOARD_SKILL_DIRECTIVE
