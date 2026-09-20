"""Unit tests for staging-token rollback assessment and inversion."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.change_event_logger import ChangeEventEnvelope
from app.services.mutation_rollback import (
    RollbackError,
    _conflict_fields,
    assess_rollback,
    rollback_staged_change,
)


@pytest.mark.unit
def test_conflict_fields_detects_divergence():
    current = {"title": "New title", "body": "same"}
    after = {"title": "Agent title", "body": "same"}
    assert _conflict_fields(current, after) == ["title"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_assess_rollback_no_events():
    mock_logger = SimpleNamespace(find_by_staging_token=AsyncMock(return_value=[]))
    import app.services.mutation_rollback as mod

    original = mod.get_change_event_logger
    mod.get_change_event_logger = lambda: mock_logger
    try:
        status = await assess_rollback(staging_token="tok-1", user_id="user-1")
    finally:
        mod.get_change_event_logger = original
    assert status["available"] is False
    assert status["reason"] == "no_events"
    assert "no recorded effect receipt" in status["message"]
    assert "may have been applied" in status["message"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_assess_rollback_available_for_entry_create(monkeypatch):
    row = SimpleNamespace(
        id="evt-1",
        log_level="CHANGE_EVENT",
        log_data={
            "ts": "2026-01-01T00:00:00Z",
            "actor_kind": "human",
            "actor_id": "user-1",
            "action": "entry.create",
            "resource_type": "Entry",
            "resource_id": "entry-1",
            "scope": "track:track-1",
            "before": None,
            "after": {"id": "entry-1", "title": "Hello"},
            "details": {"staging_token": "tok-1"},
        },
    )

    async def _find(token):
        assert token == "tok-1"
        return [row]

    monkeypatch.setattr(
        "app.services.mutation_rollback.get_change_event_logger",
        lambda: SimpleNamespace(find_by_staging_token=_find),
    )

    status = await assess_rollback(staging_token="tok-1", user_id="user-1")
    assert status["available"] is True
    assert status["event_count"] == 1
    assert status["actions"] == ["entry.create"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rollback_inverts_entry_create(monkeypatch):
    envelope = ChangeEventEnvelope(
        id="evt-1",
        ts="2026-01-01T00:00:00Z",
        actor_kind="human",
        actor_id="user-1",
        action="entry.create",
        resource_type="Entry",
        resource_id="entry-1",
        scope="track:track-1",
        before=None,
        after={"id": "entry-1", "title": "Hello"},
        details={"staging_token": "tok-1"},
    )
    row = SimpleNamespace(
        id="evt-1",
        log_level="CHANGE_EVENT",
        log_data={
            "ts": envelope.ts,
            "actor_kind": envelope.actor_kind,
            "actor_id": envelope.actor_id,
            "action": envelope.action,
            "resource_type": envelope.resource_type,
            "resource_id": envelope.resource_id,
            "scope": envelope.scope,
            "before": envelope.before,
            "after": envelope.after,
            "details": envelope.details,
        },
    )

    patch_details = AsyncMock(return_value=True)
    find_by_token = AsyncMock(return_value=[row])

    monkeypatch.setattr(
        "app.services.mutation_rollback.get_change_event_logger",
        lambda: SimpleNamespace(
            find_by_staging_token=find_by_token,
            patch_event_details=patch_details,
        ),
    )

    invert = AsyncMock(
        return_value={
            "action": "entry.create",
            "resource_id": "entry-1",
            "method": "delete",
        }
    )
    emit = AsyncMock(
        return_value=ChangeEventEnvelope(
            id="rb-1",
            ts="",
            actor_kind="human",
            actor_id="user-1",
            action="staging.rollback",
            resource_type="Entry",
            resource_id="entry-1",
            scope="track:track-1",
        )
    )

    monkeypatch.setattr("app.services.mutation_rollback._invert_event", invert)
    monkeypatch.setattr("app.services.mutation_rollback.emit_change_event", emit)

    result = await rollback_staged_change(
        staging_token="tok-1",
        user_id="user-1",
    )
    assert result["ok"] is True
    assert result["inverted"][0]["method"] == "delete"
    invert.assert_awaited_once()
    patch_details.assert_awaited_once()
    emit.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rollback_conflict_without_force(monkeypatch):
    row = SimpleNamespace(
        id="evt-2",
        log_level="CHANGE_EVENT",
        log_data={
            "ts": "2026-01-01T00:00:00Z",
            "actor_kind": "human",
            "actor_id": "user-1",
            "action": "entry.update",
            "resource_type": "Entry",
            "resource_id": "entry-1",
            "scope": "track:track-1",
            "before": {"title": "Old"},
            "after": {"title": "Agent"},
            "details": {"staging_token": "tok-2"},
        },
    )

    monkeypatch.setattr(
        "app.services.mutation_rollback.get_change_event_logger",
        lambda: SimpleNamespace(find_by_staging_token=AsyncMock(return_value=[row])),
    )
    monkeypatch.setattr(
        "app.services.mutation_rollback._invert_event",
        AsyncMock(
            side_effect=RollbackError(
                "conflict", "Entry was edited after the agent change (title)"
            )
        ),
    )

    with pytest.raises(RollbackError) as exc:
        await rollback_staged_change(staging_token="tok-2", user_id="user-1")
    assert exc.value.code == "conflict"
