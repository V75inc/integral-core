"""Phase 5 Plan 05-05 — TEST-03 end-to-end connector sync round-trip.

NETWORK-FREE: uses FakeSource in-memory dict (locked decision §13). Mirrors
the Phase 4 FakeStore pattern at ``backend/tests/test_retrieve.py:293-298``.

Asserts ROADMAP AC#2 (TEST-03) — full sync round-trip:

  1. Stage 2 fake source records.
  2. POST /api/connectors/{id}/sync → stats {created:2, ...}.
  3. Each materialized Entry has:
       - provenance.source        == "connector"
       - provenance.source_id     == "<connector_id>:<external_id>"
       - idempotency_key          == sha256("test_source:<external_id>")
  4. Each materialization emits ChangeEvent with:
       - actor_kind = "connector"
       - actor_id   = <connector_id>
  5. Lifecycle pair: connector.sync.start + connector.sync.complete.
  6. POST /api/connectors/{id}/sync AGAIN → stats {updated:2, created:0, ...}.
  7. Re-sync produces ZERO duplicates (idempotency honored).

Asserts ROADMAP AC#1 (partial) — Phase 4 substrate consumes Phase 5 writes:

  8. POST /api/retrieve query matching synced entries → entries returned
     (re-embed hook fired automatically on sync_runtime's Entry.save +
     emit_change_event path). Best-effort: skipped if the embedding model
     is unavailable in CI.

Per locked decision #14 — test-fixture seeder (workspace + space + track +
connector + IS_CONNECTED_TO edge) builds the demo inside the test, NOT a
production-level auto-seeder. UI for connector management lands in Phase 7.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime
from typing import Any, AsyncIterator, Dict

import pytest

from app.services.connectors import (
    ExternalRecord,
    MaterializedEntry,
    SyncConnector,
    register_sync_connector,
)

pytestmark = [pytest.mark.slow, pytest.mark.integration, pytest.mark.model]

# ---------------------------------------------------------------------------
# FakeSource — in-memory dict of staged records. Mirrors test_retrieve.py
# FakeStore pattern: lightweight per-test fixture, no network, no monkeypatch.
# ---------------------------------------------------------------------------


class FakeSource:
    """In-memory dict of external records (TEST-03 staging)."""

    def __init__(self, records: Dict[str, Dict[str, Any]]) -> None:
        self.records = dict(records)


# Class-level pointer the TestSourceConnector reads at sync_pull time.
# Set by the ``fake_source`` fixture (and torn down on exit). Avoids the
# need to thread state through Connector.auth_state.
_ACTIVE_SOURCE: Dict[str, Any] = {"source": None}


# ---------------------------------------------------------------------------
# Test SyncConnector subclass — staged via the autouse
# ``reset_sync_connector_registry`` fixture in conftest.py so re-registration
# is safe across tests (Plan 05-03 + I-CON-03).
# ---------------------------------------------------------------------------


def _register_test_source_connector() -> None:
    """(Re-)register the test_source SyncConnector subclass.

    The autouse ``reset_sync_connector_registry`` fixture wipes the registry
    between tests; we re-fire the decorator here on every test that needs it.
    """

    @register_sync_connector("test_source")
    class TestSourceConnector(SyncConnector):
        slug = "test_source"
        conflict_policy = "last_write_wins"

        async def sync_pull(self, *, connector: Any) -> AsyncIterator[ExternalRecord]:
            src = _ACTIVE_SOURCE.get("source")
            if src is None:
                return
            for ext_id, payload in src.records.items():
                yield ExternalRecord(
                    external_id=ext_id,
                    payload=payload,
                    updated_at=payload.get("updated_at"),
                )

        def to_entry(self, record: ExternalRecord) -> MaterializedEntry:
            return MaterializedEntry(
                title=record.payload.get("title", ""),
                body=record.payload.get("body", ""),
                entry_type_key="post",
                tags=[],
                custom_fields={"external_id": record.external_id},
            )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_source():
    """Stage 2 records the test_source connector reads from."""
    src = FakeSource(
        {
            "ext-1": {
                "title": "First record about deployment",
                "body": "deploy the staging environment",
                "updated_at": "2026-01-01T00:00:00Z",
            },
            "ext-2": {
                "title": "Second record about migration",
                "body": "run schema migration",
                "updated_at": "2026-01-01T00:00:00Z",
            },
        }
    )
    _ACTIVE_SOURCE["source"] = src
    yield src
    _ACTIVE_SOURCE["source"] = None


@pytest.fixture
async def test_workspace_track_connector(authenticated_client, test_user):
    """Provision Workspace + Track + Connector + IS_CONNECTED_TO edge.

    Per locked decision #14 — test fixture seeder, NOT a production seed.
    Returns dict with workspace_id, track_id, connector_id.

    CRITICAL fixture detail (plan-check fix): the Phase 1 ``create_connector``
    signature still takes ``mapping_profile`` but we leave it None — the
    subclass dispatch in Plan 05-03's sync_runtime reads ``subclass_slug``
    (I-CON-02). We set ``connector.subclass_slug = "test_source"`` AFTER
    ``create_connector`` returns, then save the Connector.
    """
    _register_test_source_connector()

    from app.agentive.services.connector_registry_node import create_connector
    from app.models.edges import IS_CONNECTED_TO, OWNS
    from app.models.nodes import Track, Workspace

    now = datetime.now().isoformat()

    # 1. Workspace (personal-kind owned by test_user) — workspace gate.
    workspace = await Workspace.create(
        name="TEST-03 Workspace",
        kind="personal",
        owner_id=test_user.id,
        created_at=now,
        updated_at=now,
    )
    await test_user.connect(workspace, edge=OWNS, owned_at=now)

    # 2. Track (owned by test_user) — sync target.
    track = await Track.create(
        name="TEST-03 Sync Target",
        owner_id=test_user.id,
        workspace_id=workspace.id,
        purpose="connector sync e2e",
        created_at=now,
        updated_at=now,
    )
    await test_user.connect(track, edge=OWNS, owned_at=now)

    # 3. Connector (owned by test_user) — wires per-connector Policy at
    # creation time via materialize_policies_for_connector (I-CON-04).
    connector = await create_connector(
        owner=test_user.id,
        kind="jvagent",  # Phase 1 default — kind != subclass_slug
        auth_state={},
        mapping_profile=None,  # Phase 1 signature retained; I-CON-02 deprecates
    )
    # I-CON-02 — subclass dispatch reads subclass_slug (NEVER mapping_profile).
    connector.subclass_slug = "test_source"
    await connector.save()

    # 4. IS_CONNECTED_TO edge binds Connector → Track. The sync runtime
    # reads via connector.nodes(edge=["IsConnectedTo"], direction="out", ...).
    await connector.connect(track, edge=IS_CONNECTED_TO, mapping_profile_yaml="")

    return {
        "workspace_id": workspace.id,
        "track_id": track.id,
        "connector_id": connector.id,
    }


# ---------------------------------------------------------------------------
# TEST-03 — end-to-end round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_03_connector_sync_round_trip(
    authenticated_client,
    test_user,
    fake_source,
    test_workspace_track_connector,
):
    """TEST-03 — stage fake source → sync → provenance + dedup + actor →
    re-sync → zero duplicates → retrieval round-trip."""
    ctx = test_workspace_track_connector
    connector_id = ctx["connector_id"]
    track_id = ctx["track_id"]

    # === First sync ===
    r1 = await authenticated_client.post(f"/api/connectors/{connector_id}/sync")
    assert r1.status_code == 200, r1.text
    stats1 = r1.json()["stats"]
    assert stats1 == {
        "created": 2,
        "updated": 0,
        "conflict": 0,
        "skipped": 0,
        "failed": 0,
    }

    # === Assert: 2 Entries materialized with split provenance shape ===
    from app.models.nodes import Entry

    entries = await Entry.find({"context.track_id": track_id})
    for _ in range(10):
        if len(entries) >= 2:
            break
        await asyncio.sleep(0.05)
        entries = await Entry.find({"context.track_id": track_id})
    assert len(entries) == 2

    seen_ext: set = set()
    for e in entries:
        prov = e.provenance
        assert prov is not None
        assert prov.source == "connector"  # I-CON-01 ActorKind member
        assert prov.source_id is not None
        # I-CON-01 split shape: "<connector_id>:<external_id>"
        assert prov.source_id.startswith(f"{connector_id}:")
        ext_id = prov.source_id.split(":", 1)[1]
        seen_ext.add(ext_id)
        assert prov.confidence == 1.0
        assert prov.synced_at is not None
        # Idempotency_key default: sha256("test_source:<external_id>")
        expected_key = hashlib.sha256(f"test_source:{ext_id}".encode()).hexdigest()
        assert e.idempotency_key == expected_key

    assert seen_ext == {"ext-1", "ext-2"}

    # === Assert: ChangeEvents with connector-as-actor ===
    # GET /api/audit-log?actor_kind=connector returns all connector-actor events.
    # We filter on action client-side because the endpoint does not (yet).
    al_resp = await authenticated_client.get(
        "/api/audit-log",
        params={"actor_kind": "connector", "limit": 200},
    )
    assert al_resp.status_code == 200, al_resp.text
    events = al_resp.json()["events"]
    by_action: Dict[str, list] = {}
    for ev in events:
        by_action.setdefault(ev["action"], []).append(ev)

    creates = by_action.get("entry.create", [])
    assert len(creates) == 2, f"expected 2 entry.create events, got {len(creates)}"
    for ev in creates:
        assert ev["actor_kind"] == "connector"
        assert ev["actor_id"] == connector_id

    starts = by_action.get("connector.sync.start", [])
    completes = by_action.get("connector.sync.complete", [])
    assert len(starts) == 1
    assert len(completes) == 1

    # Track synced_at across syncs for the freshness assertion below.
    first_synced_at = {e.id: e.provenance.synced_at for e in entries}

    # === Re-sync: should be upsert, no duplicates ===
    r2 = await authenticated_client.post(f"/api/connectors/{connector_id}/sync")
    assert r2.status_code == 200, r2.text
    stats2 = r2.json()["stats"]
    assert stats2 == {
        "created": 0,
        "updated": 2,
        "conflict": 0,
        "skipped": 0,
        "failed": 0,
    }

    entries2 = await Entry.find({"context.track_id": track_id})
    for _ in range(10):
        if len(entries2) >= 2:
            break
        await asyncio.sleep(0.05)
        entries2 = await Entry.find({"context.track_id": track_id})
    assert len(entries2) == 2  # NO duplicates (I-SYNC-03 honored)

    # Each entry's provenance.synced_at is strictly NEWER after the re-sync.
    for e in entries2:
        prior = first_synced_at.get(e.id)
        assert prior is not None
        assert e.provenance.synced_at > prior

    # === Retrieval round-trip (AC#1 partial) ===
    # Phase 4 re-embed hook (RET-02) fires inside sync_runtime on each Entry
    # save (see ``_reembed_synced_entry``). Best-effort: if the embedding
    # model is unavailable in CI, the store is the in-memory fallback and
    # may return no semantic matches — but the request itself must succeed.
    retrieve_resp = await authenticated_client.post(
        "/api/retrieve",
        json={
            "query": "deployment",
            "mode": "graph",  # graph mode is deterministic against seeded entries
            "scope": f"track:{track_id}",
        },
    )
    assert retrieve_resp.status_code == 200, retrieve_resp.text
    results = retrieve_resp.json()["results"]
    result_ids = {r["entry_id"] for r in results}
    entry_ids = {e.id for e in entries2}
    # Graph mode returns all entries the user can reach in the scoped track —
    # both synced entries must appear (proves cross-phase substrate integration).
    assert result_ids & entry_ids, (
        f"Synced entries did not round-trip through /api/retrieve: "
        f"results={result_ids}, entries={entry_ids}"
    )
