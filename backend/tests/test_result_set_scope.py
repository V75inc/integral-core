"""W3.6: a follow-up narrows the prior page and re-reads current values."""

import pytest

from app.agentive.services.query_spec import QuerySpecError, execute_query_spec
from app.models.query_result_set import QueryResultSet
from app.schemas.query_spec import QuerySpec
from app.services.agent_insights import query_entries
from tests.test_query_spec import _QueryNode


def _entry(entry_id: str, status: str, revision: int = 1) -> _QueryNode:
    node = _QueryNode(
        id=entry_id,
        workspace_id="ws-scope",
        title=f"title-{entry_id}",
        status=status,
    )
    node.track_id = "n.Track.rentals"
    node.schema_revision = revision
    node.custom_fields = {}
    return node


async def _first_page(monkeypatch, rows):
    async def accessible_entries(_user_id, workspace_id=None, **_kwargs):
        assert workspace_id == "ws-scope"
        return list(rows)

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries",
        accessible_entries,
    )
    result = await execute_query_spec(
        principal_id="user-scope",
        workspace_id="ws-scope",
        spec=QuerySpec(resource="entry", select=["id", "title", "status"], limit=2),
    )
    assert [item["id"] for item in result.items] == ["a", "b"]
    assert result.scope is None
    return result


@pytest.mark.asyncio
async def test_follow_up_narrows_the_original_page(monkeypatch):
    """A later row and a non-matching status stay out. Values are current."""
    later = _entry("c", "overdue")
    first, second = _entry("a", "open"), _entry("b", "overdue")
    page = await _first_page(monkeypatch, [first, second, later])
    first.status = "overdue"
    second.status = "open"

    async def accessible_entries(_user_id, workspace_id=None, **_kwargs):
        return [first, second, later]

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries",
        accessible_entries,
    )
    narrowed = await execute_query_spec(
        principal_id="user-scope",
        workspace_id="ws-scope",
        result_set_id=page.result_set_id,
        spec=QuerySpec(
            resource="entry",
            select=["id", "status"],
            filters=[{"field": "status", "op": "eq", "value": "overdue"}],
            limit=10,
        ),
    )
    assert [item["id"] for item in narrowed.items] == ["a"]
    assert narrowed.items[0]["status"] == "overdue"
    assert "c" not in [item["id"] for item in narrowed.items]
    assert narrowed.scope["membership_at"]
    assert narrowed.scope["value_read_at"]
    assert narrowed.scope["absent_ids"] == []
    assert "title-a" not in str(narrowed.scope)


@pytest.mark.asyncio
async def test_expired_wrong_principal_and_schema_drift_return_no_rows(monkeypatch):
    """Those refusals do not rerun the query and do not include titles."""
    first, second = _entry("a", "open"), _entry("b", "open")
    page = await _first_page(monkeypatch, [first, second])
    record = (await QueryResultSet.find({"context.result_set_id": page.result_set_id}))[
        0
    ]
    record.expires_at = "2000-01-01T00:00:00+00:00"
    await record.save()

    calls = {"n": 0}

    async def accessible_entries(*_args, **_kwargs):
        calls["n"] += 1
        return [first, second]

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries",
        accessible_entries,
    )
    with pytest.raises(QuerySpecError, match="result_set.expired"):
        await execute_query_spec(
            principal_id="user-scope",
            workspace_id="ws-scope",
            result_set_id=page.result_set_id,
            spec=QuerySpec(resource="entry", select=["id", "title"], limit=10),
        )
    assert calls["n"] == 0

    record.expires_at = "2099-01-01T00:00:00+00:00"
    await record.save()
    with pytest.raises(QuerySpecError, match="result_set.wrong_principal") as wrong:
        await execute_query_spec(
            principal_id="someone-else",
            workspace_id="ws-scope",
            result_set_id=page.result_set_id,
            spec=QuerySpec(resource="entry", select=["id", "title"], limit=10),
        )
    assert "title-a" not in str(wrong.value)
    assert calls["n"] == 0

    first.schema_revision = 9
    with pytest.raises(QuerySpecError, match="result_set.schema_drift") as drifted:
        await execute_query_spec(
            principal_id="user-scope",
            workspace_id="ws-scope",
            result_set_id=page.result_set_id,
            spec=QuerySpec(resource="entry", select=["id", "title"], limit=10),
        )
    assert "title-a" not in str(drifted.value)


@pytest.mark.asyncio
async def test_deleted_and_revoked_members_are_named_without_values(monkeypatch):
    """A missing id is absent. An unreadable id is excluded. Neither leaks a title."""
    first, second = _entry("a", "open"), _entry("b", "open")
    page = await _first_page(monkeypatch, [first, second])

    async def accessible_entries(*_args, **_kwargs):
        return [second]

    async def get_entry(item_id):
        if item_id == "a":
            return None
        return first

    monkeypatch.setattr(
        "app.agentive.services.query_spec.get_user_accessible_entries",
        accessible_entries,
    )
    monkeypatch.setattr("app.models.nodes.Entry.get", get_entry)
    narrowed = await execute_query_spec(
        principal_id="user-scope",
        workspace_id="ws-scope",
        result_set_id=page.result_set_id,
        spec=QuerySpec(resource="entry", select=["id", "title"], limit=10),
    )
    assert [item["id"] for item in narrowed.items] == ["b"]
    assert narrowed.scope["absent_ids"] == ["a"]
    assert "title-a" not in str(narrowed.scope)

    async def still_there(item_id):
        return first if item_id == "a" else None

    monkeypatch.setattr("app.models.nodes.Entry.get", still_there)
    revoked = await execute_query_spec(
        principal_id="user-scope",
        workspace_id="ws-scope",
        result_set_id=page.result_set_id,
        spec=QuerySpec(resource="entry", select=["id", "title"], limit=10),
    )
    assert [item["id"] for item in revoked.items] == ["b"]
    assert revoked.scope["excluded_ids"] == ["a"]
    assert "title-a" not in str(revoked.scope)


class _Track:
    def __init__(self):
        self.id = "n.Track.rentals"
        self.title = "Rentals"
        self.workspace_id = "ws-scope"


@pytest.mark.asyncio
async def test_query_entries_does_not_add_rows_outside_the_set(monkeypatch):
    """query_entries with the prior id keeps a newer entry out of the total."""
    first, second, newer = _entry("a", "open"), _entry("b", "open"), _entry("c", "open")
    page = await _first_page(monkeypatch, [first, second])
    track = _Track()

    async def tracks(_user_id):
        return [track]

    async def entries(_user_id, track_id, **_kwargs):
        return [first, second, newer] if track_id == track.id else []

    monkeypatch.setattr("app.services.permissions.get_user_accessible_tracks", tracks)
    monkeypatch.setattr("app.services.permissions.get_user_accessible_entries", entries)
    result = await query_entries(
        user_id="user-scope",
        workspace_id="ws-scope",
        track_id=track.id,
        result_set_id=page.result_set_id,
        limit=10,
    )
    assert [row["id"] for row in result["entries"]] == ["a", "b"]
    assert result["scope"]["result_set_id"] == page.result_set_id
