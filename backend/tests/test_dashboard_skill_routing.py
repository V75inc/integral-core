"""Host routes dashboard compose/adjust through use_skill, not find_tool."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.ai_chat import (
    _focused_dashboard_id,
    _is_dashboard_skill_request,
)


def test_focused_dashboard_id_reads_page_metadata() -> None:
    ctx = SimpleNamespace(metadata={"focused_dashboard_id": "n.Dashboard.abc"})
    assert _focused_dashboard_id(ctx) == "n.Dashboard.abc"
    assert _focused_dashboard_id(None) is None
    assert _focused_dashboard_id(SimpleNamespace(metadata=None)) is None


@pytest.mark.parametrize(
    "text,focused,expected",
    [
        ("add a pie chart to this dashboard", None, True),
        ("adjust the dashboard layout", None, True),
        ("update dashboard widgets", None, True),
        ("create a dashboard for this app", None, True),
        ("what is a dashboard?", None, False),
        ("show me the feed", None, False),
        ("add a chart", "n.Dashboard.abc", True),
        ("rename it", "n.Dashboard.abc", True),
        ("hello", "n.Dashboard.abc", False),
        # Focus alone is not enough without mutate intent.
        ("thanks", "n.Dashboard.abc", False),
    ],
)
def test_dashboard_skill_request_detection(
    text: str, focused: str | None, expected: bool
) -> None:
    ctx = (
        SimpleNamespace(metadata={"focused_dashboard_id": focused})
        if focused
        else None
    )
    assert _is_dashboard_skill_request(text, ctx) is expected
