"""W3.5: the planner picks an instrument and a real window. It reads nothing."""

from datetime import datetime, timezone

from app.services.query_plan import build_query_plan

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


def test_of_those_stays_on_the_prior_result_set():
    """A follow-up names the prior result set instead of a new scan."""
    plan = build_query_plan("of those, which are overdue", now=_NOW)
    assert plan["instrument"] == "integral_query_entries"
    assert plan["scope"]["result_set_id"] == "<prior result_set_id>"
    assert "result_set_id" in plan["limits"][0]


def test_query_spec_cursor_schema_is_a_single_type():
    """jvagent rejects a type list, which hid integral_query_spec from the model."""
    from app.agentive.tooling.catalogue import build_tool_catalogue

    tools = {tool["name"]: tool for tool in build_tool_catalogue()}
    cursor = tools["integral_query_spec"]["input_schema"]["properties"]["spec"][
        "properties"
    ]["cursor"]
    assert cursor["type"] == "string"


def test_bad_input_is_not_an_empty_result():
    """A missing question or a bad zone is an error, not zero records."""
    missing = build_query_plan("  ", now=_NOW)
    assert missing["error"] == "question_required"
    assert "instrument" not in missing
    zone = build_query_plan("highest deal", timezone_name="Not/AZone", now=_NOW)
    assert zone["error"] == "invalid_timezone"
    assert "value" not in zone
