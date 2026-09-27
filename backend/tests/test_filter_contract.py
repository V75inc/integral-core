"""Saved views, query_entries, and counts share {field, op, value}."""

import pytest

from app.exceptions import BadRequestError
from app.services.entry_listing import view_filter_clauses
from app.services.operational_model_compile import normalize_view_config
from app.services.query_filters import entry_matches_filters


def test_saved_view_op_in_and_not_in_become_executable_clauses():
    """A view stored with op or operator keeps in and not_in as real clauses."""
    saved = normalize_view_config(
        "table",
        {
            "filters": [
                {"field": "status", "op": "in", "value": ["open", "hold"]},
                {"field": "status", "operator": "not_in", "value": ["void"]},
            ]
        },
    )["filters"]

    assert saved == [
        {"field": "status", "op": "in", "value": ["open", "hold"]},
        {"field": "status", "op": "not_in", "value": ["void"]},
    ]
    assert view_filter_clauses(saved) == [
        {"context.status": {"$in": ["open", "hold"]}},
        {"context.status": {"$nin": ["void"]}},
    ]


def test_unknown_view_operator_is_refused_before_it_can_be_stored():
    """An operator the listing cannot run is a save error, not a dropped filter."""
    with pytest.raises(BadRequestError, match="unsupported filter operator"):
        normalize_view_config(
            "feed",
            {"filters": [{"field": "status", "op": "matches", "value": "open"}]},
        )


def test_query_entries_advertises_the_filter_list():
    """The query and count tools publish the {field, op, value} list."""
    from app.agentive.tooling.catalogue import build_tool_catalogue

    catalogue = {tool["name"]: tool for tool in build_tool_catalogue()}
    filters = catalogue["integral_query_entries"]["input_schema"]["properties"][
        "filters"
    ]
    counted = catalogue["integral_count_entries"]["input_schema"]["properties"][
        "filters"
    ]

    assert filters["type"] == "array"
    assert "gte" in filters["items"]["properties"]["op"]["enum"]
    assert counted["type"] == "array"


def test_query_entries_date_range_keeps_only_the_window():
    """A custom date window and not_in keep only the matching rows."""
    window = [
        {"field": "custom_fields.due", "op": "gte", "value": "2026-04-01"},
        {"field": "custom_fields.due", "op": "lte", "value": "2026-04-30"},
    ]
    inside = {"custom_fields": {"due": "2026-04-15"}}
    after = {"custom_fields": {"due": "2026-05-01"}}

    assert entry_matches_filters(inside, window)
    assert not entry_matches_filters(after, window)
    assert not entry_matches_filters(
        {"status": "done"},
        [{"field": "status", "operator": "not_in", "value": ["done", "void"]}],
    )
