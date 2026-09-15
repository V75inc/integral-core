"""Unit tests for mutation provenance ContextVar binding."""

from __future__ import annotations

import pytest

from app.services.change_event import emit_change_event
from app.services.mutation_provenance import (
    bind_mutation_provenance,
    provenance_details,
    reset_mutation_provenance,
)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_emit_change_event_merges_provenance_details(monkeypatch):
    captured = {}

    async def _fake_persist(self, envelope):
        captured["details"] = envelope.details
        envelope.id = "evt-1"
        return None

    monkeypatch.setattr(
        "app.services.change_event.is_change_event_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.change_event.get_change_event_logger",
        lambda: type("L", (), {"persist": _fake_persist})(),
    )
    monkeypatch.setattr(
        "app.services.change_event.BROADCAST_SKIP_ACTIONS",
        {"policy.deny", "anchor.cascade", "approval.expired", "staging.rollback"},
    )

    _, reset = bind_mutation_provenance(
        staging_token="tok-abc",
        thread_id="thread-1",
        message_id="msg-1",
    )
    try:
        assert provenance_details() == {
            "staging_token": "tok-abc",
            "thread_id": "thread-1",
            "message_id": "msg-1",
        }
        await emit_change_event(
            actor_kind="human",
            actor_id="user-1",
            action="entry.create",
            resource_type="Entry",
            resource_id="entry-1",
            before=None,
            after={"id": "entry-1"},
            scope="track:track-1",
        )
    finally:
        reset_mutation_provenance(reset)

    assert captured["details"]["staging_token"] == "tok-abc"
    assert captured["details"]["thread_id"] == "thread-1"
    assert captured["details"]["message_id"] == "msg-1"
