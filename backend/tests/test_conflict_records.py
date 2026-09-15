"""Phase 5 Plan 05-03 — Conflict Node + helpers + REST regression suite.

Covers:
- Conflict Node CRUD + jvspatial round-trip
- create_conflict_record helper snapshots the local Entry
- list_conflicts filter by connector_id / status
- resolve_conflict mutates only the Conflict row (caller applies the resolution)
- REST surface: GET /api/conflicts, GET /api/conflicts/{id}, POST resolve
"""

from __future__ import annotations

from typing import List

import pytest

from app.models.nodes import Conflict, Entry
from app.services.connectors.conflict_records import (
    create_conflict_record,
    list_conflicts,
    resolve_conflict,
)

# ---------------------------------------------------------------------------
# create_conflict_record — snapshots the entry, persists Conflict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_conflict_record_snapshots_entry():
    """The helper captures title / body / tags / custom_fields / updated_at."""
    entry = await Entry.create(
        title="local-edit",
        body="local-body",
        track_id="trk-1",
        tags=["a", "b"],
        custom_fields={"k": "v"},
        updated_at="2026-05-01T00:00:00+00:00",
    )
    external = {"title": "ext-v", "body": "ext-body", "tags": [], "custom_fields": {}}

    c = await create_conflict_record(
        connector_id="c-1",
        entry=entry,
        external_snapshot=external,
    )
    assert c.id
    assert c.status == "open"
    assert c.entry_id == entry.id
    assert c.connector_id == "c-1"
    assert c.local_snapshot["title"] == "local-edit"
    assert c.local_snapshot["body"] == "local-body"
    assert c.local_snapshot["tags"] == ["a", "b"]
    assert c.local_snapshot["custom_fields"] == {"k": "v"}
    assert c.local_snapshot["updated_at"] == "2026-05-01T00:00:00+00:00"
    assert c.external_snapshot["title"] == "ext-v"
    assert c.detected_at is not None


# ---------------------------------------------------------------------------
# list_conflicts — filter by connector_id + status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_conflicts_filter_by_connector_id():
    """Two conflicts on connector-A, one on connector-B → filter returns 2."""
    entry = await Entry.create(title="t", track_id="trk-1")
    await create_conflict_record(connector_id="A", entry=entry, external_snapshot={})
    await create_conflict_record(connector_id="A", entry=entry, external_snapshot={})
    await create_conflict_record(connector_id="B", entry=entry, external_snapshot={})

    a_list = await list_conflicts(connector_id="A")
    assert len(a_list) == 2

    b_list = await list_conflicts(connector_id="B")
    assert len(b_list) == 1


@pytest.mark.asyncio
async def test_list_conflicts_filter_by_status():
    """Open + resolved → filter returns just the open one."""
    entry = await Entry.create(title="t", track_id="trk-1")
    c1 = await create_conflict_record(
        connector_id="C", entry=entry, external_snapshot={}
    )
    c2 = await create_conflict_record(
        connector_id="C", entry=entry, external_snapshot={}
    )
    await resolve_conflict(conflict=c2, resolution="kept_local", resolver_user_id="u-1")

    open_list = await list_conflicts(connector_id="C", status="open")
    assert len(open_list) == 1
    assert open_list[0].id == c1.id

    resolved_list = await list_conflicts(connector_id="C", status="resolved")
    assert len(resolved_list) == 1
    assert resolved_list[0].id == c2.id


@pytest.mark.asyncio
async def test_list_conflicts_no_filter_returns_all():
    entry = await Entry.create(title="t", track_id="trk-1")
    await create_conflict_record(connector_id="X", entry=entry, external_snapshot={})
    await create_conflict_record(connector_id="Y", entry=entry, external_snapshot={})
    all_list = await list_conflicts()
    assert len(all_list) >= 2


# ---------------------------------------------------------------------------
# resolve_conflict — mutates only the Conflict row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_conflict_sets_resolved_fields():
    """resolve_conflict stamps resolution + resolved_at + resolved_by + status."""
    entry = await Entry.create(title="t", track_id="trk-1")
    c = await create_conflict_record(
        connector_id="R", entry=entry, external_snapshot={"title": "ext"}
    )
    assert c.status == "open"
    assert c.resolved_at is None
    assert c.resolved_by is None
    assert c.resolution == ""

    resolved = await resolve_conflict(
        conflict=c, resolution="kept_local", resolver_user_id="u-resolver"
    )
    assert resolved.status == "resolved"
    assert resolved.resolution == "kept_local"
    assert resolved.resolved_by == "u-resolver"
    assert resolved.resolved_at is not None


@pytest.mark.asyncio
async def test_resolve_conflict_rejects_unknown_resolution():
    """Unknown resolution raises ValueError (boundary catches malformed bodies)."""
    entry = await Entry.create(title="t", track_id="trk-1")
    c = await create_conflict_record(
        connector_id="R", entry=entry, external_snapshot={}
    )
    with pytest.raises(ValueError):
        await resolve_conflict(
            conflict=c, resolution="not_a_real_resolution", resolver_user_id="u-1"
        )


@pytest.mark.asyncio
async def test_resolve_conflict_does_not_mutate_entry():
    """The helper does NOT touch the Entry — caller applies the resolution
    (kept_local = no-op; applied_external = caller writes external_snapshot
    back into the Entry via the standard update path)."""
    entry = await Entry.create(title="local-pristine", track_id="trk-1")
    c = await create_conflict_record(
        connector_id="R",
        entry=entry,
        external_snapshot={"title": "ext-should-not-be-applied"},
    )
    await resolve_conflict(
        conflict=c, resolution="applied_external", resolver_user_id="u-1"
    )

    refreshed = await Entry.get(entry.id)
    # The helper did NOT touch the Entry.
    assert refreshed.title == "local-pristine"


# ---------------------------------------------------------------------------
# REST surface — drive handlers directly (no HTTP client). The auth + body
# parsing layer is stubbed via MagicMock to keep the assertions focused on
# the policy gate + resolution semantics.
# ---------------------------------------------------------------------------


from unittest.mock import AsyncMock, MagicMock, patch

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    MissingAuthenticationError,
)
from app.schemas.policy import Decision


def _make_request(*, json_body=None, user_id="u-1"):
    """Construct a MagicMock Request that mirrors the bits handlers read."""
    request = MagicMock()
    request.state.user = MagicMock(id=user_id)

    async def _json():
        return json_body or {}

    request.json = _json
    return request


@pytest.mark.asyncio
async def test_rest_list_conflicts_filter_by_connector_id():
    """REST list endpoint filters by connector_id query param."""
    from app.api.conflicts import list_all

    entry = await Entry.create(title="t", track_id="trk-1")
    await create_conflict_record(connector_id="A", entry=entry, external_snapshot={})
    await create_conflict_record(connector_id="A", entry=entry, external_snapshot={})
    await create_conflict_record(connector_id="B", entry=entry, external_snapshot={})

    async def _allow(*_a, **_k):
        return Decision(allowed=True, reason="test_bypass")

    with (
        patch("app.api.conflicts.resolve_principal_id", return_value="u-test"),
        patch("app.api.conflicts.evaluate", side_effect=_allow),
    ):
        result = await list_all(_make_request(), connector_id="A", status=None)
    assert "conflicts" in result
    assert len(result["conflicts"]) == 2


@pytest.mark.asyncio
async def test_rest_list_conflicts_auth_required():
    """No principal → 401 (MissingAuthenticationError)."""
    from app.api.conflicts import list_all

    with patch("app.api.conflicts.resolve_principal_id", return_value=None):
        with pytest.raises(MissingAuthenticationError):
            await list_all(_make_request(), connector_id=None, status=None)


@pytest.mark.asyncio
async def test_rest_get_one_conflict():
    """GET /api/conflicts/{id} returns single record."""
    from app.api.conflicts import get_one

    entry = await Entry.create(title="t", track_id="trk-1")
    c = await create_conflict_record(
        connector_id="X", entry=entry, external_snapshot={}
    )

    async def _allow(*_a, **_k):
        return Decision(allowed=True, reason="test_bypass")

    with (
        patch("app.api.conflicts.resolve_principal_id", return_value="u-test"),
        patch("app.api.conflicts.evaluate", side_effect=_allow),
    ):
        result = await get_one(_make_request(), conflict_id=c.id)
    assert result["conflict"]["id"] == c.id


@pytest.mark.asyncio
async def test_rest_get_one_conflict_not_found():
    from app.api.conflicts import get_one
    from app.api.errors import ResourceNotFoundError as RNF

    with patch("app.api.conflicts.resolve_principal_id", return_value="u-test"):
        with pytest.raises(RNF):
            await get_one(_make_request(), conflict_id="nonexistent-id")


@pytest.mark.asyncio
async def test_rest_resolve_rejects_unknown_resolution():
    """Unknown resolution → BadRequestError (400)."""
    from app.api.conflicts import resolve

    entry = await Entry.create(title="t", track_id="trk-1")
    c = await create_conflict_record(
        connector_id="X", entry=entry, external_snapshot={}
    )

    with patch("app.api.conflicts.resolve_principal_id", return_value="u-test"):
        with pytest.raises(BadRequestError):
            await resolve(
                _make_request(json_body={"resolution": "no_such_resolution"}),
                conflict_id=c.id,
            )


@pytest.mark.asyncio
async def test_rest_resolve_policy_gate_denies():
    """policy_engine returns deny → InsufficientPermissionsError (403)."""
    from app.api.conflicts import resolve

    entry = await Entry.create(title="t", track_id="trk-1")
    c = await create_conflict_record(
        connector_id="X", entry=entry, external_snapshot={}
    )

    async def _deny(*a, **k):
        return Decision(allowed=False, reason="policy_denied")

    with (
        patch("app.api.conflicts.resolve_principal_id", return_value="u-test"),
        patch("app.api.conflicts.evaluate", side_effect=_deny),
    ):
        with pytest.raises(InsufficientPermissionsError):
            await resolve(
                _make_request(json_body={"resolution": "kept_local"}),
                conflict_id=c.id,
            )


@pytest.mark.asyncio
async def test_rest_resolve_kept_local_marks_resolved_no_entry_mutation():
    """kept_local: Conflict resolved; Entry NOT mutated."""
    from app.api.conflicts import resolve

    entry = await Entry.create(title="local-keep", track_id="trk-1")
    c = await create_conflict_record(
        connector_id="X",
        entry=entry,
        external_snapshot={"title": "should-not-apply"},
    )

    async def _allow(*a, **k):
        return Decision(allowed=True, reason="test_bypass")

    with (
        patch("app.api.conflicts.resolve_principal_id", return_value="u-resolver"),
        patch("app.api.conflicts.evaluate", side_effect=_allow),
    ):
        result = await resolve(
            _make_request(json_body={"resolution": "kept_local"}),
            conflict_id=c.id,
        )

    assert result["status"] == "resolved"
    assert result["resolution"] == "kept_local"

    refreshed_entry = await Entry.get(entry.id)
    assert refreshed_entry.title == "local-keep"

    refreshed_conflict = await c.__class__.get(c.id)
    assert refreshed_conflict.status == "resolved"
    assert refreshed_conflict.resolved_by == "u-resolver"


@pytest.mark.asyncio
async def test_rest_resolve_applied_external_mutates_entry_and_emits_entry_update():
    """applied_external: Entry overwritten; single entry.update ChangeEvent emitted.

    I-SYNC-04: no separate conflict.resolve ChangeEventAction — the
    entry.update emit carries details.resolved_conflict_id for the audit trail.
    """
    from app.api.conflicts import resolve

    entry = await Entry.create(title="local", body="local-body", track_id="trk-1")
    c = await create_conflict_record(
        connector_id="X",
        entry=entry,
        external_snapshot={"title": "external-applied", "body": "external-body"},
    )

    async def _allow(*a, **k):
        return Decision(allowed=True, reason="test_bypass")

    emits: list = []

    async def _spy_emit(*a, **k):
        emits.append(k)
        return None

    with (
        patch("app.api.conflicts.resolve_principal_id", return_value="u-resolver"),
        patch("app.api.conflicts.evaluate", side_effect=_allow),
        patch("app.api.conflicts.emit_change_event", side_effect=_spy_emit),
    ):
        result = await resolve(
            _make_request(json_body={"resolution": "applied_external"}),
            conflict_id=c.id,
        )

    assert result["status"] == "resolved"
    refreshed = await Entry.get(entry.id)
    assert refreshed.title == "external-applied"
    assert refreshed.body == "external-body"

    # Exactly one entry.update emit with details.resolved_conflict_id.
    assert len(emits) == 1
    e = emits[0]
    assert e["action"] == "entry.update"
    assert e["actor_kind"] == "human"
    assert e["actor_id"] == "u-resolver"
    assert e.get("details", {}).get("resolved_conflict_id") == c.id
    assert e.get("details", {}).get("resolution") == "applied_external"


# ---------------------------------------------------------------------------
# REST surface — connector sync trigger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rest_sync_connector_auth_required():
    """Unauthenticated POST → 401."""
    from app.api.connectors import sync_connector

    with patch("app.api.connectors.resolve_principal_id", return_value=None):
        with pytest.raises(MissingAuthenticationError):
            await sync_connector(_make_request(), connector_id="c-1")


@pytest.mark.asyncio
async def test_rest_sync_connector_not_found():
    """Unknown connector → 404."""
    from app.api.connectors import sync_connector
    from app.api.errors import ResourceNotFoundError as RNF

    with patch("app.api.connectors.resolve_principal_id", return_value="u-test"):
        with pytest.raises(RNF):
            await sync_connector(_make_request(), connector_id="never-existed")


@pytest.mark.asyncio
async def test_rest_sync_connector_policy_denied():
    """policy_engine returns deny → 403."""
    from app.agentive.nodes import Connector
    from app.api.connectors import sync_connector

    connector = await Connector.create(subclass_slug="dummy", owner="u-1")

    async def _deny(*a, **k):
        return Decision(allowed=False, reason="policy_denied")

    with (
        patch("app.api.connectors.resolve_principal_id", return_value="u-test"),
        patch("app.api.connectors.evaluate", side_effect=_deny),
    ):
        with pytest.raises(InsufficientPermissionsError):
            await sync_connector(_make_request(), connector_id=connector.id)


@pytest.mark.asyncio
async def test_rest_sync_connector_happy_path_returns_stats():
    """Auth + policy allow + valid connector → sync_one_connector called, stats returned."""
    from app.agentive.nodes import Connector
    from app.api.connectors import sync_connector

    connector = await Connector.create(subclass_slug="dummy", owner="u-1")

    async def _allow(*a, **k):
        return Decision(allowed=True, reason="test_bypass")

    stats_payload = {
        "created": 2,
        "updated": 0,
        "conflict": 0,
        "skipped": 0,
        "failed": 0,
    }

    async def _fake_sync(c):
        return stats_payload

    with (
        patch("app.api.connectors.resolve_principal_id", return_value="u-test"),
        patch("app.api.connectors.evaluate", side_effect=_allow),
        patch(
            "app.services.connectors.sync_runtime.sync_one_connector",
            side_effect=_fake_sync,
        ),
    ):
        result = await sync_connector(_make_request(), connector_id=connector.id)
    assert result["connector_id"] == connector.id
    assert result["stats"] == stats_payload
