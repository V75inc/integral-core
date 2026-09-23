"""Relative dates remain live in saved views and dashboard filters."""

from datetime import datetime, timezone

import pytest

from app.services.entry_listing import _view_filter_to_clause
from app.services.query_filters import filter_matches
from app.services.relative_date_filters import (
    normalize_relative_date,
    resolve_relative_date,
)


def test_scaffold_placeholders_become_durable_typed_dates():
    plan = {"filters": [{"value": "{{now_plus_7}}"}, {"value": "{{now}}"}]}
    assert normalize_relative_date(plan) == {
        "filters": [
            {"value": {"relative_date_days": 7}},
            {"value": {"relative_date_days": 0}},
        ]
    }


def test_saved_view_and_dashboard_evaluate_same_relative_day(monkeypatch):
    from app.services import relative_date_filters

    marker = {"relative_date_days": 7}
    now = datetime(2026, 9, 22, 23, 30, tzinfo=timezone.utc)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz or timezone.utc)

    monkeypatch.setattr(relative_date_filters, "datetime", FixedDatetime)
    assert resolve_relative_date(marker, now=now) == "2026-09-29"
    assert _view_filter_to_clause("custom_fields.due_date", "lte", marker) == {
        "context.custom_fields.due_date": {"$lte": resolve_relative_date(marker)}
    }
    assert filter_matches("2026-09-21", op="lt", expected={"relative_date_days": 0})
    assert filter_matches("2026-09-30", op="gt", expected=marker)


@pytest.mark.parametrize("days", [True, 366, "7", -366])
def test_invalid_relative_offsets_fail_closed(days):
    with pytest.raises(ValueError, match="relative_date_days"):
        resolve_relative_date({"relative_date_days": days})
