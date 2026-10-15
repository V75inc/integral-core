"""Bounded history, honest outages and immutable request workspace selection."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.errors import QueryUnavailableError
from app.services import event_history
from app.services.change_event_logger import ChangeEventLogger
from app.services.event_history_cursor import decode_history_cursor
from app.services.pagination import decode_cursor, encode_cursor

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


def log_row(index):
    return SimpleNamespace(
        id=f"event-{index:05}",
        logged_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
        path="track:track",
        event_code="entry.update",
        log_data={"resource_id": "entry", "resource_type": "Entry"},
    )


async def test_denied_rows_have_a_bounded_scan_and_continuation(monkeypatch):
    calls = []

    async def page(**kwargs):
        calls.append(kwargs)
        return [log_row((len(calls) - 1) * 100 + i) for i in range(100)]

    monkeypatch.setattr(event_history, "_in_workspace", AsyncMock(return_value=False))
    result = await event_history.read_event_history(
        logger=SimpleNamespace(find_page=page),
        user_id="user",
        workspace_id="bound",
        action="audit_log.read",
        cursor=None,
        limit=50,
        descending=True,
    )
    assert result["events"] == []
    assert len(calls) == 10
    assert all(c["limit"] == 100 and c["workspace_id"] == "bound" for c in calls)
    assert result["has_more"] is True and result["scan_complete"] is False
    position = decode_history_cursor(
        result["next_cursor"],
        binding={
            "principal_id": "user",
            "workspace_id": "bound",
            "action": "audit_log.read",
            "descending": True,
            "scope": "",
            "actor_kind": "",
            "event_action": "",
            "resource_id": "",
            "resource_type": "",
        },
    )
    assert decode_cursor(position)[0] == "event-00999"
    assert "event-00999" not in result["next_cursor"]


async def test_page_stops_at_last_delivered_row_without_skipping_window(monkeypatch):
    monkeypatch.setattr(event_history, "_in_workspace", AsyncMock(return_value=True))

    async def prepare(user_id, event, **kwargs):
        return event.to_wire_flat()

    monkeypatch.setattr(event_history, "prepare_event_payload", prepare)
    logger = SimpleNamespace(
        find_page=AsyncMock(return_value=[log_row(i) for i in range(100)])
    )
    result = await event_history.read_event_history(
        logger=logger,
        user_id="user",
        workspace_id="bound",
        action="audit_log.read",
        cursor=None,
        limit=3,
        descending=False,
    )
    assert [e["id"] for e in result["events"]] == [
        "event-00000",
        "event-00001",
        "event-00002",
    ]
    position = decode_history_cursor(
        result["next_cursor"],
        binding={
            "principal_id": "user",
            "workspace_id": "bound",
            "action": "audit_log.read",
            "descending": False,
            "scope": "",
            "actor_kind": "",
            "event_action": "",
            "resource_id": "",
            "resource_type": "",
        },
    )
    assert decode_cursor(position)[0] == "event-00002"
    assert "total" not in result


async def test_logger_pushes_keyset_sort_and_limit_into_store(monkeypatch):
    db = SimpleNamespace(find=AsyncMock(return_value=[]))
    logger = ChangeEventLogger()
    monkeypatch.setattr(
        logger, "_get_log_context", lambda: SimpleNamespace(database=db)
    )
    await logger.find_page(
        cursor=encode_cursor("event", "2026-10-14T00:00:00+00:00"),
        limit=9999,
        descending=True,
        workspace_id="bound",
    )
    args, kwargs = db.find.await_args
    assert args[0] == "object"
    assert kwargs == {"limit": 200, "sort": [("context.logged_at", -1), ("id", -1)]}
    assert args[1]["$and"][-1] == {
        "$or": [
            {"context.logged_at": {"$lt": "2026-10-14T00:00:00+00:00"}},
            {"context.logged_at": "2026-10-14T00:00:00+00:00", "id": {"$lt": "event"}},
        ]
    }
    assert db.find.await_count == 2
    assert db.find.await_args_list[0].args[1]["$and"][1] == {
        "context.log_data.workspace_id": {"$in": ["bound"]}
    }
    assert db.find.await_args_list[1].args[1]["$and"][1] == {
        "context.log_data.workspace_id": {"$exists": False}
    }


@pytest.mark.parametrize("descending", [True, False])
async def test_attributed_and_legacy_streams_merge_without_skipping(
    monkeypatch, descending
):
    from jvspatial.core.context import get_default_context

    context = get_default_context()
    logger = ChangeEventLogger()
    monkeypatch.setattr(logger, "_get_log_context", lambda: context)
    timestamp = "2026-10-14T00:00:00+00:00"

    def row(index, attributed):
        return {
            "id": f"o.DBLog.merge-{index}",
            "entity": "DBLog",
            "context": {
                "logged_at": timestamp,
                "log_level": "CHANGE_EVENT",
                "event_code": "entry.update",
                "path": "track:track",
                "method": "",
                "log_data": {"workspace_id": "bound"} if attributed else {},
            },
        }

    current = [row(index, True) for index in [0, 2, 4]]
    legacy = [row(index, False) for index in [1, 3, 5]]
    if descending:
        current.reverse()
        legacy.reverse()
    find = AsyncMock(side_effect=[current, legacy])
    monkeypatch.setattr(context.database, "find", find)
    result = await logger.find_page(
        cursor=None, limit=3, descending=descending, workspace_id="bound"
    )
    expected = [5, 4, 3] if descending else [0, 1, 2]
    assert [item.id for item in result] == [
        f"o.DBLog.merge-{index}" for index in expected
    ]
    assert all(call.kwargs["limit"] == 3 for call in find.await_args_list)


@pytest.mark.parametrize("missing", [True, False])
async def test_store_outage_is_not_an_empty_history(monkeypatch, missing):
    logger = ChangeEventLogger()
    context = (
        None
        if missing
        else SimpleNamespace(
            database=SimpleNamespace(find=AsyncMock(side_effect=OSError("offline")))
        )
    )
    monkeypatch.setattr(logger, "_get_log_context", lambda: context)
    with pytest.raises(QueryUnavailableError):
        await logger.find_page(cursor=None, limit=10, descending=False)


@pytest.mark.parametrize("path", ["/api/audit-log", "/api/events"])
async def test_authorized_other_workspace_is_excluded_from_bound_feed(
    authenticated_client, path
):
    async def create(name):
        response = await authenticated_client.post(
            "/api/workspaces", json={"name": name}
        )
        assert response.status_code == 200, response.text
        workspace_id = response.json()["workspace"]["id"]
        response = await authenticated_client.post(
            "/api/tracks",
            json={"title": name, "workspace_id": workspace_id},
            headers={"X-Integral-Scope": f"ws:{workspace_id}"},
        )
        assert response.status_code == 200, response.text
        return workspace_id, response.json()["track"]["id"]

    bound, _ = await create("Workspace A")
    other, track = await create("Workspace B")
    scoped = await authenticated_client.get(
        path,
        params={"scope": f"track:{track}"},
        headers={"X-Integral-Scope": f"ws:{bound}"},
    )
    assert scoped.status_code == 200, scoped.text
    assert scoped.json()["events"] == []
    own = await authenticated_client.get(
        path,
        params={"scope": f"track:{track}"},
        headers={"X-Integral-Scope": f"ws:{other}"},
    )
    assert own.status_code == 200, own.text
    assert any(e["resource_id"] == track for e in own.json()["events"])
