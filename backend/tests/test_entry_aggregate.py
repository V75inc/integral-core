"""W3.1 aggregate semantics: exact totals, refusals, no partial numbers."""

from decimal import Decimal

import pytest

from app.schemas.entry_aggregate import AggregateSpec
from app.services.entry_aggregate import aggregate_entries, aggregate_rows


def _row(ident, **fields):
    row = {
        "id": ident,
        "custom_fields": {},
        "created_at": fields.pop("created_at", None),
    }
    for key in ("status", "track_id", "type_id", "title", "updated_at"):
        if key in fields:
            row[key] = fields.pop(key)
    row["custom_fields"] = fields
    return row


def _spec(op, **kwargs):
    return AggregateSpec(op=op, **kwargs)


def test_nulls_and_empty():
    """Value aggregates skip nulls; empty sum is zero and empty avg is null."""
    rows = [
        _row("a", amount="10"),
        _row("b", amount=None),
        _row("c"),
    ]
    summed = aggregate_rows(rows, _spec("sum", field="amount"))
    assert summed["value"] == "10"
    assert summed["nulls_ignored"] == 2
    assert aggregate_rows([], _spec("sum", field="amount"))["value"] == "0"
    assert aggregate_rows([], _spec("avg", field="amount"))["value"] is None
    assert aggregate_rows([], _spec("min", field="amount"))["value"] is None
    assert aggregate_rows([], _spec("count"))["value"] == 0


def test_duplicate_rows_do_not_inflate_and_conflicts_refuse():
    """Identical copies collapse; conflicting copies refuse with no total."""
    rows = [
        _row("a", amount="10"),
        _row("a", amount="10"),
        _row("b", amount="5"),
    ]
    summed = aggregate_rows(rows, _spec("sum", field="amount"))
    assert summed["value"] == "15"
    assert summed["count"] == 2
    conflict = aggregate_rows(
        [_row("a", amount="10"), _row("a", amount="11")],
        _spec("sum", field="amount"),
    )
    assert conflict["error"] == "duplicate_conflict"
    assert "value" not in conflict


def test_multi_value_group_counts_once_in_the_total():
    """One entry in two groups contributes once to the grand total."""
    rows = [_row("a", amount="10", tags=["alpha", "beta"])]
    grouped = aggregate_rows(rows, _spec("sum", field="amount", group_by="tags"))
    assert grouped["value"] == "10"
    assert {item["key"]: item["value"] for item in grouped["groups"]} == {
        "alpha": "10",
        "beta": "10",
    }


def test_decimal_precision_and_display_rounding():
    """The exact decimal stays in value; display applies the declared scale."""
    rows = [_row("a", amount="0.10"), _row("b", amount="0.20")]
    summed = aggregate_rows(rows, _spec("sum", field="amount", scale=2))
    assert Decimal(summed["value"]) == Decimal("0.30")
    assert summed["display"] == "0.30"


def test_incompatible_currency_and_invalid_number():
    """Mixed currencies and non-numeric values are refused before a total."""
    mixed = aggregate_rows(
        [
            _row("a", amount={"amount": "1", "currency": "USD"}),
            _row("b", amount={"amount": "2", "currency": "EUR"}),
        ],
        _spec("sum", field="amount"),
    )
    assert mixed["error"] == "incompatible_currency"
    assert "value" not in mixed
    plain = aggregate_rows(
        [_row("a", amount="1"), _row("b", amount={"amount": "2", "currency": "USD"})],
        _spec("sum", field="amount"),
    )
    assert plain["error"] == "incompatible_currency"
    bad = aggregate_rows([_row("a", amount="nope")], _spec("avg", field="amount"))
    assert bad["error"] == "invalid_number"
    nonfinite = aggregate_rows([_row("a", amount="NaN")], _spec("sum", field="amount"))
    assert nonfinite["error"] == "invalid_number"


def test_date_only_keeps_calendar_day_and_datetime_uses_zone():
    """Date-only values ignore the zone; datetimes bucket on the zone's calendar day."""
    rows = [
        _row("a", due="2026-03-08"),
        {"id": "b", "created_at": "2026-03-08T03:30:00Z", "custom_fields": {}},
        {"id": "c", "created_at": "2026-03-08T05:30:00Z", "custom_fields": {}},
    ]
    by_due = aggregate_rows(
        rows[:1],
        _spec("count", group_by="date:due", timezone="America/Los_Angeles"),
    )
    assert by_due["groups"][0]["key"] == "2026-03-08"
    by_created = aggregate_rows(
        rows[1:],
        _spec("count", group_by="date", timezone="America/New_York"),
    )
    keys = [item["key"] for item in by_created["groups"]]
    assert keys == ["2026-03-07", "2026-03-08"]


def test_over_budget_returns_no_total():
    """A scan past the budget returns over_budget and no partial number."""
    rows = [_row(str(i), amount="1") for i in range(3)]
    refused = aggregate_rows(rows, _spec("sum", field="amount", budget=2))
    assert refused["error"] == "over_budget"
    assert refused["total"] == 3
    assert "value" not in refused


def test_distinct_ignores_nulls():
    """Distinct counts non-null typed values once."""
    rows = [_row("a", status="open"), _row("b", status="open"), _row("c", status=None)]
    distinct = aggregate_rows(rows, _spec("distinct", field="status"))
    assert distinct["value"] == 1
    assert distinct["nulls_ignored"] == 1


@pytest.mark.asyncio
async def test_aggregate_entries_passes_boundary_refusal(monkeypatch):
    """A packaged-track refusal does not come back as an empty total."""

    async def _query(**_kwargs):
        return {"entries": [], "complete": True, "refused": [{"track_id": "t1"}]}

    monkeypatch.setattr("app.services.agent_insights.query_entries", _query)
    result = await aggregate_entries("user", "count", track_id="t1")
    assert result["error"] == "refused"
    assert "value" not in result


@pytest.mark.asyncio
async def test_aggregate_entries_computes_from_the_scan(monkeypatch):
    """The service aggregates the authorized scan and keeps the boundary note."""

    async def _query(**_kwargs):
        return {
            "entries": [_row("a", amount="2"), _row("b", amount="3")],
            "total": 2,
            "boundary": {"omitted": 1},
        }

    monkeypatch.setattr("app.services.agent_insights.query_entries", _query)
    result = await aggregate_entries("user", "sum", field="amount")
    assert result["value"] == "5"
    assert result["boundary"]["omitted"] == 1


@pytest.mark.asyncio
async def test_aggregate_reads_beyond_one_page_and_refuses_over_budget(monkeypatch):
    """An exact total governs the whole authorized set, not a default page."""
    seen = []

    async def _query(**kwargs):
        seen.append(kwargs)
        rows = [_row(str(i), amount="0.1") for i in range(501)]
        return {"entries": rows[: kwargs["limit"]], "total": len(rows)}

    monkeypatch.setattr("app.services.agent_insights.query_entries", _query)
    result = await aggregate_entries("user", "sum", field="amount", budget=600)
    assert result["value"] == "50.1"
    assert result["count"] == 501
    assert seen[-1]["limit"] == 601

    refused = await aggregate_entries("user", "sum", field="amount", budget=500)
    assert refused["error"] == "over_budget"
    assert refused["total"] == 501
    assert "value" not in refused
