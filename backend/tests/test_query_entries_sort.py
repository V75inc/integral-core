"""W3.2: query_entries orders a business field before it pages."""

from __future__ import annotations

import pytest

from app.services import permissions
from app.services.agent_insights import (
    _entry_persistence_candidate_query,
    query_entries,
)


class _Track:
    def __init__(self, id_: str, title: str):
        self.id = id_
        self.title = title
        self.workspace_id = "n.Workspace.one"


class _Entry:
    def __init__(self, id_: str, title: str, value):
        self.id = id_
        self.title = title
        self.track_id = "n.Track.deals"
        self.updated_at = "2026-01-01T00:00:00+00:00"
        self.created_at = self.updated_at
        self.body = ""
        self.tags = []
        self.type_id = ""
        self.status = "open"
        self.author_id = "u1"
        self.custom_fields = {} if value is None else {"value": value}


@pytest.fixture
def deals(monkeypatch):
    track = _Track("n.Track.deals", "Deals")
    rows = [
        _Entry("n.Entry.low", "Low", 2),
        _Entry("n.Entry.high", "High", 10),
        _Entry("n.Entry.mid", "Mid", 9),
        _Entry("n.Entry.blank", "Blank", None),
    ]

    async def tracks(_user_id: str):
        return [track]

    async def entries(_user_id: str, track_id: str, **_kwargs):
        return list(rows) if track_id == track.id else []

    monkeypatch.setattr("app.services.permissions.get_user_accessible_tracks", tracks)
    monkeypatch.setattr("app.services.permissions.get_user_accessible_entries", entries)


@pytest.mark.asyncio
async def test_highest_value_is_the_first_row_past_one_page(deals):
    """limit 1 still returns the maximum, not whichever row was updated last."""
    result = await query_entries(
        user_id="u1",
        track_id="n.Track.deals",
        sort_by="custom_fields.value",
        sort_dir="desc",
        limit=1,
    )
    assert result["total"] == 4
    assert result["entries"][0]["id"] == "n.Entry.high"
    assert result["entries"][0]["custom_fields"]["value"] == 10


@pytest.mark.asyncio
async def test_numeric_order_and_nulls_last(deals):
    """10 sorts above 9 and 2. A missing value stays after the numbers."""
    result = await query_entries(
        user_id="u1",
        track_id="n.Track.deals",
        sort_by="custom_fields.value",
        sort_dir="desc",
        limit=10,
    )
    assert [row["id"] for row in result["entries"]] == [
        "n.Entry.high",
        "n.Entry.mid",
        "n.Entry.low",
        "n.Entry.blank",
    ]
    ascending = await query_entries(
        user_id="u1",
        track_id="n.Track.deals",
        sort_by="custom_fields.value",
        sort_dir="asc",
        limit=10,
    )
    assert [row["id"] for row in ascending["entries"]][-1] == "n.Entry.blank"
    assert [row["id"] for row in ascending["entries"]][:3] == [
        "n.Entry.low",
        "n.Entry.mid",
        "n.Entry.high",
    ]


@pytest.mark.asyncio
async def test_scalar_equality_is_pushed_to_accessible_entry_lookup(deals, monkeypatch):
    original = permissions.get_user_accessible_entries
    pushed_queries = []

    async def capture(user_id, track_id, **kwargs):
        pushed_queries.append(kwargs.get("candidate_query"))
        return await original(user_id, track_id, **kwargs)

    monkeypatch.setattr("app.services.permissions.get_user_accessible_entries", capture)
    result = await query_entries(
        user_id="u1",
        track_id="n.Track.deals",
        filters=[{"field": "title", "op": "eq", "value": "High"}],
    )

    assert result["total"] == 1
    assert result["entries"][0]["id"] == "n.Entry.high"
    assert pushed_queries == [{"context.title": "High"}]


def test_candidate_filter_compiler_pushes_only_scalar_contract_fields():
    assert _entry_persistence_candidate_query(
        [
            {"field": "title", "op": "in", "value": ["High", "Low"]},
            {"field": "updated_at", "op": "gte", "value": "2026-01-01"},
            {"field": "custom_fields.value", "op": "eq", "value": 10},
        ]
    ) == {
        "$and": [
            {"context.title": {"$in": ["High", "Low"]}},
            {"context.updated_at": {"$gte": "2026-01-01"}},
        ]
    }


@pytest.mark.asyncio
async def test_unknown_sort_is_rejected(deals):
    """A sort key that is neither platform nor custom_fields.<key> does not silently reorder."""
    with pytest.raises(ValueError, match="sort_by"):
        await query_entries(
            user_id="u1",
            track_id="n.Track.deals",
            sort_by="owner",
        )
