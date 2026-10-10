"""Independent PostgreSQL ownership, rollback and typed connector contracts."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.services.connectors.sync_lease import (
    SyncLeaseLost,
    claim_sync,
    release_sync,
    sync_effect,
)

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


def _binding():
    suffix = uuid.uuid4().hex
    return SimpleNamespace(
        id=f"binding-{suffix}", workspace_id=f"ws-{suffix}", owner=f"owner-{suffix}"
    )


async def test_two_workers_claim_one_binding(postgres_raw_db):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        binding = _binding()
        claims = await asyncio.gather(claim_sync(binding), claim_sync(binding))
        owners = [claim for claim in claims if claim is not None]
        assert len(owners) == 1
        async with sync_effect(owners[0]) as graph:
            await graph.database.insert_if_absent(
                "object",
                {
                    "id": "o.SyncEffect." + binding.id,
                    "entity": "SyncEffectProbe",
                    "context": {"owner": owners[0].token},
                },
            )
        assert await postgres_raw_db.get("object", "o.SyncEffect." + binding.id)
        await release_sync(owners[0])


async def test_expired_worker_cannot_write_or_release_successor(postgres_raw_db):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        binding = _binding()
        old = await claim_sync(binding, lease_seconds=0.02)
        await asyncio.sleep(0.04)
        current = await claim_sync(binding)
        assert current is not None and current.fence > old.fence
        with pytest.raises(SyncLeaseLost):
            async with sync_effect(old) as graph:
                await graph.database.insert_if_absent(
                    "object",
                    {
                        "id": "o.StaleEffect." + binding.id,
                        "entity": "SyncEffectProbe",
                        "context": {},
                    },
                )
        assert (
            await postgres_raw_db.get("object", "o.StaleEffect." + binding.id) is None
        )
        await release_sync(old)
        assert await claim_sync(binding) is None
        async with sync_effect(current):
            pass
        await release_sync(current)


async def test_record_effect_rolls_back_with_its_fence(postgres_raw_db):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        binding = _binding()
        owner = await claim_sync(binding)
        oid = "o.RollbackEffect." + binding.id
        with pytest.raises(RuntimeError, match="injected"):
            async with sync_effect(owner) as graph:
                await graph.database.insert_if_absent(
                    "object", {"id": oid, "entity": "SyncEffectProbe", "context": {}}
                )
                raise RuntimeError("injected write failure")
        assert await postgres_raw_db.get("object", oid) is None
        async with sync_effect(owner):
            pass
        await release_sync(owner)


async def test_sync_commits_typed_rooted_entry_and_durable_event(postgres_raw_db):
    from app.agentive.nodes import Connector
    from app.models.edges import CONTAINS, IS_OF_TYPE
    from app.models.nodes import Entry, EntryType, Track
    from app.services.connectors import ExternalRecord, reset_sync_registry
    from app.services.connectors.sync_runtime import sync_one_connector
    from tests.test_connector_sync_runtime import (
        _bind_connector_to_track,
        _build_fake_connector_class,
    )

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        suffix = uuid.uuid4().hex
        slug = "pg-source-" + suffix
        records = [ExternalRecord(external_id="external", payload={"title": "First"})]
        _build_fake_connector_class(slug=slug, records=records)
        track = await Track.create(title="Source", workspace_id="temporary")
        connector = await Connector.create(
            subclass_slug=slug, owner="pg-owner-" + suffix
        )
        await _bind_connector_to_track(connector, track)
        try:
            first = await sync_one_connector(connector)
            assert first["created"] == 1
            entries = await Entry.find({"context.track_id": track.id})
            assert len(entries) == 1
            entry = entries[0]
            assert entry.type_id
            assert (
                await entry.count_nodes(
                    edge=[IS_OF_TYPE], node=[EntryType], direction="out"
                )
                == 1
            )
            assert (
                await entry.count_nodes(edge=[CONTAINS], node=[Track], direction="in")
                == 1
            )
            assert (await sync_one_connector(connector))["updated"] == 1
            fresh = await Entry.get(entry.id)
            assert fresh.record_revision == 2
            facts = await postgres_raw_db.find(
                "object",
                {
                    "entity": "OperationEventOutbox",
                    "context.event.resource_id": entry.id,
                },
            )
            assert len(facts) == 2
            assert {r["context"]["event"]["actor_id"] for r in facts} == {connector.id}
        finally:
            reset_sync_registry()


async def test_typed_commands_fence_concurrent_stale_updates(postgres_raw_db):
    from app.agentive.nodes import Connector
    from app.api.errors import ResourceConflictError
    from app.models.nodes import Entry, Track
    from app.services.entry_create import create_entry_in_track
    from app.services.entry_update import update_entry_in_track
    from tests.test_connector_sync_runtime import _bind_connector_to_track

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        track = await Track.create(title="Commands", workspace_id="temporary")
        connector = await Connector.create(
            subclass_slug="command-proof", owner=uuid.uuid4().hex
        )
        await _bind_connector_to_track(connector, track)
        entry = await create_entry_in_track(
            track=track,
            user_id=connector.owner,
            workspace_id=connector.workspace_id,
            title="Before",
            change_event_sink=lambda event: None,
        )
        results = await asyncio.gather(
            *[
                update_entry_in_track(
                    entry_id=entry.id,
                    user_id=connector.owner,
                    workspace_id=connector.workspace_id,
                    title=title,
                    expected_record_revision=1,
                    change_event_sink=lambda event: None,
                )
                for title in ("Worker A", "Worker B")
            ],
            return_exceptions=True,
        )
        assert sum(isinstance(result, Entry) for result in results) == 1
        assert sum(isinstance(result, ResourceConflictError) for result in results) == 1
        fresh = await Entry.get(entry.id)
        assert fresh.record_revision == 2
        assert fresh.title in {"Worker A", "Worker B"}


async def test_entry_hook_failure_rolls_back_node_and_edges(
    postgres_raw_db, monkeypatch
):
    from app.agentive.nodes import Connector
    from app.models.nodes import Entry, Track
    from app.services.entry_create import create_entry_in_track
    from tests.test_connector_sync_runtime import _bind_connector_to_track

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        track = await Track.create(title="Rollback", workspace_id="temporary")
        connector = await Connector.create(
            subclass_slug="rollback-proof", owner=uuid.uuid4().hex
        )
        await _bind_connector_to_track(connector, track)

        async def fail(**kwargs):
            raise RuntimeError("injected entry hook failure")

        monkeypatch.setattr("app.services.entry_create.run_entry_save_hooks", fail)
        before_edges = await postgres_raw_db.count("edge", {})
        with pytest.raises(RuntimeError, match="injected entry hook"):
            await create_entry_in_track(
                track=track,
                user_id=connector.owner,
                workspace_id=connector.workspace_id,
                title="Never committed",
                custom_fields={},
                change_event_sink=lambda event: None,
            )
        assert await Entry.find({"context.track_id": track.id}) == []
        assert await postgres_raw_db.count("edge", {}) == before_edges
