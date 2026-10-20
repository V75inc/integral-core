"""Index intent commits or rolls back with canonical Entry commands."""

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.models.nodes import Entry
from app.services.entry_write_scope import entry_write_scope
from app.services.retrieval.index_definition import digest, embedding_profile
from app.services.retrieval.pgvector_driver import PgVectorEmbeddingStore
from tests.test_resource_location import graph

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def test_canonical_writes_commit_index_intent_and_rollback_together(
    postgres_raw_db, monkeypatch
):
    import app.api  # Initialize route registration before importing command services.
    from app.services import retrieval

    monkeypatch.setattr(
        retrieval, "get_embedding_store", lambda: PgVectorEmbeddingStore()
    )
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        user, workspace, app, track, _ = await graph()
        from app.services.entry_create import create_entry_in_track
        from app.services.entry_update import update_entry_in_track

        entry = await create_entry_in_track(
            track=track,
            user_id=user.id,
            title="Initial",
            body="Version one",
            workspace_id=workspace.id,
        )
        identity = "o.EntryIndexJob." + digest([entry.id, embedding_profile().id])
        row = await postgres_raw_db.get("object", identity)
        assert row and row["context"]["status"] == "pending"
        previous_hash = row["context"]["source_hash"]
        with pytest.raises(RuntimeError, match="rollback"):
            async with entry_write_scope(entry.id):
                await update_entry_in_track(
                    entry_id=entry.id, user_id=user.id, body="Must not commit"
                )
                raise RuntimeError("rollback")
        fresh = await Entry.find_one({"id": entry.id})
        assert fresh.body == "Version one"
        assert (await postgres_raw_db.get("object", identity))["context"][
            "source_hash"
        ] == previous_hash
        await update_entry_in_track(
            entry_id=entry.id, user_id=user.id, body="Version two"
        )
        row = await postgres_raw_db.get("object", identity)
        assert row["context"]["source_hash"] != previous_hash


async def test_index_configuration_and_audit_intent_share_commit(postgres_raw_db):
    import app.api
    from app.models.nodes import Track
    from app.services.retrieval.index_management import configure_index

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        user, workspace, app, track, _ = await graph()
        predicate = {
            "entity": "OperationEventOutbox",
            "context.event.resource_id": track.id,
        }
        with pytest.raises(RuntimeError, match="rollback"):
            async with entry_write_scope(track.id):
                await configure_index(
                    user.id, track.id, {"fields": ["body"]}, workspace.id
                )
                raise RuntimeError("rollback")
        assert (await Track.find_one({"id": track.id})).search_index_revision == 0
        assert await postgres_raw_db.find("object", predicate) == []
        result = await configure_index(
            user.id, track.id, {"fields": ["body"]}, workspace.id
        )
        assert result["revision"] == 1
        events = await postgres_raw_db.find("object", predicate)
        assert len(events) == 1
        assert events[0]["context"]["event"]["after"]["search_index_revision"] == 1
