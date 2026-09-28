"""W3.5: the planner picks an instrument and a real window. It reads nothing."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.query_plan import (
    build_query_plan,
    focused_aggregate_result,
    insights_plan_preamble,
)

_NOW = datetime(2026, 3, 8, 4, 30, tzinfo=timezone.utc)


def test_value_ranking_is_a_sort_not_a_search():
    """Highest-value stays a sorted query. A semantic miss is not the plan."""
    plan = build_query_plan("highest-value deal", now=_NOW)
    assert plan["instrument"] == "integral_query_spec"
    assert plan["sort"][0]["direction"] == "desc"
    assert plan["limit"] == 1
    assert "not found" in plan["on_failure"]
    assert "integral_query" in plan["limits"][0]


def test_smallest_and_top_n():
    """The extreme direction and the requested width are in the plan."""
    small = build_query_plan("smallest invoice", now=_NOW)
    assert small["sort"][0]["direction"] == "asc"
    top = build_query_plan("top 3 largest deals", now=_NOW)
    assert top["limit"] == 3
    assert top["sort"][0]["direction"] == "desc"


def test_totals_and_counts_do_not_share_an_instrument():
    """A sum is an aggregate. A how-many is a count."""
    total = build_query_plan("what's the total value", now=_NOW)
    assert total["instrument"] == "integral_aggregate"
    assert total["aggregation"]["op"] == "sum"
    count = build_query_plan("how many open deals by status", now=_NOW)
    assert count["instrument"] == "integral_count_entries"
    assert count["aggregation"]["group_by"] == "status"
    number = build_query_plan("total number of deals", now=_NOW)
    assert number["instrument"] == "integral_count_entries"


def test_today_uses_the_supplied_zone():
    """04:30 UTC is still the previous calendar day in New York."""
    ny = build_query_plan("activity today", timezone_name="America/New_York", now=_NOW)
    utc = build_query_plan("activity today", timezone_name="UTC", now=_NOW)
    assert ny["instrument"] == "integral_activity_digest"
    assert ny["window"]["since"].startswith("2026-03-07")
    assert utc["window"]["since"].startswith("2026-03-08")
    week = build_query_plan("what is happening this week", now=_NOW)
    assert week["window"]["label"] == "this week"
    assert week["window"]["since"] < week["window"]["until"]


def test_relations_and_search_stay_distinct():
    """Links are get_related. Open wording is hybrid retrieval."""
    links = build_query_plan("what is this connected to", now=_NOW)
    assert links["instrument"] == "integral_get_related"
    assert links["traversal"]["direction"] == "both"
    assert links["traversal"]["include_anchors"] is True
    inbound = build_query_plan("what points at this entry", now=_NOW)
    assert inbound["traversal"]["direction"] == "in"
    found = build_query_plan("find anything about widgets", now=_NOW)
    assert found["instrument"] == "integral_query"


def test_total_price_names_the_field_and_the_track():
    """A total names the field and the track so the model can execute it."""
    plan = build_query_plan("what is the total price of rentals", now=_NOW)
    assert plan["instrument"] == "integral_aggregate"
    assert plan["aggregation"]["field"] == "price"
    assert plan["track_hint"] == "rentals"
    assert any("Do not ask the user" in line for line in plan["limits"])


def test_natural_field_total_and_distinct_route_to_aggregate():
    total = build_query_plan(
        "What is the exact total of the Amount field across the two Posts in this Amounts track?",
        now=_NOW,
    )
    assert total["instrument"] == "integral_aggregate"
    assert total["aggregation"] == {"op": "sum", "field": "amount"}
    assert total["track_hint"] == "amounts"

    distinct = build_query_plan(
        "How many different Status values are there in this Equipment track?",
        now=_NOW,
    )
    assert distinct["instrument"] == "integral_aggregate"
    assert distinct["aggregation"] == {"op": "distinct", "field": "status"}
    assert distinct["track_hint"] == "equipment"


def test_of_those_stays_on_the_prior_result_set():
    """A follow-up names the prior result set instead of a new scan."""
    plan = build_query_plan("of those, which are overdue", now=_NOW)
    assert plan["instrument"] == "integral_query_entries"
    assert plan["scope"]["result_set_id"] == "<prior result_set_id>"
    assert "result_set_id" in plan["limits"][0]


def test_query_spec_cursor_schema_is_a_single_type():
    """A type list hid integral_query_spec because the runtime rejects it."""
    from app.agentive.tooling.catalogue import build_tool_catalogue

    tools = {tool["name"]: tool for tool in build_tool_catalogue()}
    cursor = tools["integral_query_spec"]["input_schema"]["properties"]["spec"][
        "properties"
    ]["cursor"]
    assert cursor["type"] == "string"


def test_host_preamble_covers_totals_and_skips_ordinary_chat():
    """A total is planned before the model chooses a tool. A greeting is not."""
    preamble = insights_plan_preamble("what is the total price of rentals", now=_NOW)
    assert "integral_aggregate" in preamble
    assert "Do not ask where the records are kept" in preamble
    assert insights_plan_preamble("hello there", now=_NOW) == ""
    assert insights_plan_preamble("of those, which are overdue", now=_NOW) == ""


def test_bad_input_is_not_an_empty_result():
    """A missing question or a bad zone is an error, not zero records."""
    missing = build_query_plan("  ", now=_NOW)
    assert missing["error"] == "question_required"
    assert "instrument" not in missing
    zone = build_query_plan("highest deal", timezone_name="Not/AZone", now=_NOW)
    assert zone["error"] == "invalid_timezone"
    assert "value" not in zone


@pytest.mark.asyncio
async def test_focused_aggregate_executes_under_bound_identity(monkeypatch):
    calls = []

    async def fake_aggregate(**kwargs):
        calls.append(kwargs)
        return {"op": "sum", "value": "30.3", "total": 2}

    monkeypatch.setattr(
        "app.services.entry_aggregate.aggregate_entries", fake_aggregate
    )
    result = await focused_aggregate_result(
        "What is the sum of Amount in this track?",
        user_id="user-1",
        track_id="track-1",
        workspace_id="workspace-1",
    )
    assert result == {"op": "sum", "value": "30.3", "total": 2}
    assert calls == [
        {
            "user_id": "user-1",
            "op": "sum",
            "field": "amount",
            "track_id": "track-1",
            "workspace_id": "workspace-1",
        }
    ]
    assert (
        await focused_aggregate_result(
            "hello", user_id="user-1", track_id="track-1", workspace_id=None
        )
        is None
    )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_named_aggregate_resolves_one_accessible_track(monkeypatch):
    async def accessible(_user_id):
        return [
            SimpleNamespace(id="track-a", title="Amounts", workspace_id="ws-a"),
            SimpleNamespace(id="track-b", title="Amounts", workspace_id="ws-b"),
        ]

    async def aggregate(**kwargs):
        return kwargs

    monkeypatch.setattr(
        "app.services.permissions.get_user_accessible_tracks", accessible
    )
    monkeypatch.setattr("app.services.entry_aggregate.aggregate_entries", aggregate)
    result = await focused_aggregate_result(
        "What is the sum of Amount in the Amounts track?",
        user_id="user-1",
        track_id="",
        workspace_id="ws-a",
    )
    assert result["track_id"] == "track-a"
    override = await focused_aggregate_result(
        "What is the sum of Amount in the Amounts track?",
        user_id="user-1",
        track_id="track-b",
        workspace_id="ws-a",
    )
    assert override["track_id"] == "track-a"
    assert (
        await focused_aggregate_result(
            "What is the sum of Amount in the Amounts track?",
            user_id="user-1",
            track_id="",
            workspace_id=None,
        )
        is None
    )
