"""ChangeEvent emit + snapshot equality tests (Plan 02-02 Task 1).

Verifies the contract for the single-emission path (D-05) + sync inline emit (D-06):
- Snapshot before/after correctness for create / update / delete
- D-09 ChangeEventAction Literal rejects free strings at the Pydantic boundary
- D-10 Actor.kind reuses ActorKind from app.schemas.provenance
- Row is durable in the logging DB before emit returns (D-06 sync)
- ``snapshot_reclaimed_at`` defaults to None until reclaim runs (Plan 02-04)

Storage shape: ChangeEvents persist as ``DBLog`` rows with
``log_level="CHANGE_EVENT"`` (see app/services/change_event_logger.py).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.audit import Actor, ChangeEventResponse
from app.schemas.provenance import ActorKind
from app.services.change_event import emit_change_event
from app.services.change_event_logger import (
    CHANGE_EVENT_LOG_LEVEL,
    envelope_from_dblog,
    get_change_event_logger,
)

# ---------------------------------------------------------------------------
# Pydantic boundary regression — D-09 + D-10
# ---------------------------------------------------------------------------


def test_actor_kind_accepts_canonical_values():
    """D-10: Actor.kind reuses ActorKind from provenance — same Literal members."""
    for kind in ("human", "agent", "connector", "system"):
        a = Actor(kind=kind, id="x")
        assert a.kind == kind


def test_actor_kind_rejects_invalid_string():
    """D-10: invalid kind raises ValidationError (proves Literal enforcement)."""
    with pytest.raises(ValidationError):
        Actor(kind="invalid", id="x")  # type: ignore[arg-type]


def test_actor_rejects_extra_fields():
    """Strict-keys 422 — Actor cannot accept unknown keys."""
    with pytest.raises(ValidationError):
        Actor(kind="human", id="x", role="admin")  # type: ignore[call-arg]


def test_change_event_action_rejects_unknown_literal():
    """D-09: ChangeEventAction Literal rejects unknown action strings."""
    with pytest.raises(ValidationError):
        ChangeEventResponse(
            id="ce-1",
            ts="2026-05-01T00:00:00+00:00",  # type: ignore[arg-type]
            actor=Actor(kind="human", id="u-1"),
            action="not.in.literal",  # type: ignore[arg-type]
            resource_type="Entry",
            resource_id="e-1",
            scope="track:t-1",
        )


def test_change_event_action_accepts_canonical_action():
    """D-09: a known Literal member round-trips via Pydantic."""
    r = ChangeEventResponse(
        id="ce-1",
        ts="2026-05-01T00:00:00+00:00",  # type: ignore[arg-type]
        actor=Actor(kind="human", id="u-1"),
        action="entry.create",
        resource_type="Entry",
        resource_id="e-1",
        scope="track:t-1",
    )
    assert r.action == "entry.create"


def test_actor_kind_literal_is_shared_with_provenance():
    """D-10: Actor.kind is the SAME ActorKind Literal as Provenance.source."""
    from typing import get_args

    assert set(get_args(ActorKind)) == {"human", "agent", "connector", "system"}


# ---------------------------------------------------------------------------
# emit_change_event — sync inline emit + snapshot semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_create_persists_with_after_only():
    """Create-shape: before=None, after populated, durable before return (D-06)."""
    envelope = await emit_change_event(
        actor_kind="human",
        actor_id="u-1",
        action="entry.create",
        resource_type="Entry",
        resource_id="e-1",
        before=None,
        after={"id": "e-1", "title": "hello"},
        scope="track:t-1",
    )
    assert envelope.id  # row durable

    # Reload from the logging DB to confirm sync-emit (no race window).
    row = await get_change_event_logger().get(envelope.id)
    assert row is not None
    assert row.log_level == CHANGE_EVENT_LOG_LEVEL
    reloaded = envelope_from_dblog(row)
    assert reloaded.before is None
    assert reloaded.after == {"id": "e-1", "title": "hello"}
    assert reloaded.action == "entry.create"
    assert reloaded.actor_kind == "human"
    assert reloaded.actor_id == "u-1"
    assert reloaded.scope == "track:t-1"


@pytest.mark.asyncio
async def test_emit_update_persists_before_and_after():
    """Update-shape: before == prior snapshot, after == new snapshot."""
    prior = {"id": "e-2", "title": "v1"}
    new = {"id": "e-2", "title": "v2"}
    envelope = await emit_change_event(
        actor_kind="human",
        actor_id="u-1",
        action="entry.update",
        resource_type="Entry",
        resource_id="e-2",
        before=prior,
        after=new,
        scope="track:t-1",
    )
    row = await get_change_event_logger().get(envelope.id)
    assert row is not None
    reloaded = envelope_from_dblog(row)
    assert reloaded.before == prior
    assert reloaded.after == new


@pytest.mark.asyncio
async def test_emit_delete_persists_with_before_only():
    """Delete-shape: before populated, after=None."""
    prior = {"id": "e-3", "title": "doomed"}
    envelope = await emit_change_event(
        actor_kind="human",
        actor_id="u-1",
        action="entry.delete",
        resource_type="Entry",
        resource_id="e-3",
        before=prior,
        after=None,
        scope="track:t-1",
    )
    row = await get_change_event_logger().get(envelope.id)
    assert row is not None
    reloaded = envelope_from_dblog(row)
    assert reloaded.before == prior
    assert reloaded.after is None


@pytest.mark.asyncio
async def test_emit_ttl_field_defaults_none():
    """D-04: snapshot_reclaimed_at is None until the reclaim job runs."""
    envelope = await emit_change_event(
        actor_kind="system",
        actor_id="sys",
        action="entry.create",
        resource_type="Entry",
        resource_id="e-4",
        before=None,
        after={"id": "e-4"},
        scope="track:t-1",
    )
    row = await get_change_event_logger().get(envelope.id)
    assert row is not None
    reloaded = envelope_from_dblog(row)
    assert reloaded.snapshot_reclaimed_at is None


@pytest.mark.asyncio
async def test_emit_with_missing_actor_persists():
    """Missing actor User node no longer triggers edge wiring — row persists unconditionally."""
    envelope = await emit_change_event(
        actor_kind="human",
        actor_id="non-existent-user-id",
        action="entry.create",
        resource_type="Entry",
        resource_id="e-5",
        before=None,
        after={"id": "e-5"},
        scope="track:t-1",
    )
    row = await get_change_event_logger().get(envelope.id)
    assert row is not None


@pytest.mark.asyncio
async def test_emit_system_actor_persists():
    """System actor — no actor-node lookup is attempted; row persists."""
    envelope = await emit_change_event(
        actor_kind="system",
        actor_id="system",
        action="entry.create",
        resource_type="Entry",
        resource_id="e-6",
        before=None,
        after={"id": "e-6"},
        scope="user:system",
    )
    row = await get_change_event_logger().get(envelope.id)
    assert row is not None
    reloaded = envelope_from_dblog(row)
    assert reloaded.actor_kind == "system"


@pytest.mark.asyncio
async def test_emit_with_missing_resource_persists():
    """Missing target resource no longer triggers edge wiring — row persists."""
    envelope = await emit_change_event(
        actor_kind="human",
        actor_id="u-1",
        action="entry.create",
        resource_type="Entry",
        resource_id="non-existent-entry-id",
        before=None,
        after={"id": "non-existent-entry-id"},
        scope="track:t-1",
    )
    row = await get_change_event_logger().get(envelope.id)
    assert row is not None


@pytest.mark.asyncio
async def test_emit_stores_log_level_change_event(monkeypatch):
    """Every persisted row has log_level='CHANGE_EVENT' (discriminator)."""
    envelope = await emit_change_event(
        actor_kind="human",
        actor_id="u-disc",
        action="entry.create",
        resource_type="Entry",
        resource_id="e-disc",
        before=None,
        after={"id": "e-disc"},
        scope="track:t-disc",
    )
    row = await get_change_event_logger().get(envelope.id)
    assert row is not None
    assert row.log_level == CHANGE_EVENT_LOG_LEVEL
    assert row.event_code == "entry.create"
    assert row.path == "track:t-disc"


@pytest.mark.asyncio
async def test_emit_is_noop_when_disabled(monkeypatch):
    """CHANGE_EVENT_ENABLED=False: emit returns unpersisted envelope."""
    from app.config import settings

    monkeypatch.setattr(settings, "CHANGE_EVENT_ENABLED", False)
    envelope = await emit_change_event(
        actor_kind="human",
        actor_id="u-off",
        action="entry.create",
        resource_type="Entry",
        resource_id="e-off",
        before=None,
        after={"id": "e-off"},
        scope="track:t-off",
    )
    # Envelope populated for callers that inspect metadata, but no id was assigned.
    assert envelope.id == ""
    assert envelope.action == "entry.create"

    # Confirm no DBLog row was written.
    rows = await get_change_event_logger().find_all()
    assert rows == []
