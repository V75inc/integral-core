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


async def test_workspace_embedding_config_rebuild_and_audit_rollback_together(
    postgres_raw_db, monkeypatch
):
    from app.schemas.embedding_configuration import EmbeddingModelWrite
    from app.services.retrieval import embedding_configuration as service
    from tests.test_embedding_configuration import fixture

    monkeypatch.setattr(service, "overrides_supported", lambda: True)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        user, workspace, connection, body = await fixture()
        with pytest.raises(RuntimeError, match="rollback"):
            async with entry_write_scope(workspace.id):
                await service.save(user.user_id, workspace.id, body)
                raise RuntimeError("rollback")
        from app.services.workspace_model_configuration import configuration_record

        fresh = await configuration_record(workspace.id)
        assert fresh.embedding_settings_revision == 0
        assert (
            await postgres_raw_db.find(
                "node", {"entity": "WorkspaceModelConfiguration"}
            )
            == []
        )
        assert (
            await postgres_raw_db.find("edge", {"entity": "HasModelConfiguration"})
            == []
        )
        assert (
            await postgres_raw_db.find(
                "object",
                {"entity": service.REBUILD, "context.workspace_id": workspace.id},
            )
            == []
        )
        assert (
            await postgres_raw_db.find(
                "object",
                {
                    "entity": "OperationEventOutbox",
                    "context.event.resource_id": workspace.id,
                },
            )
            == []
        )
        result = await service.save(user.user_id, workspace.id, body)
        assert result["revision"] == 1
        assert (
            len(
                await postgres_raw_db.find(
                    "object",
                    {"entity": service.REBUILD, "context.workspace_id": workspace.id},
                )
            )
            == 1
        )
        events = await postgres_raw_db.find(
            "object",
            {
                "entity": "OperationEventOutbox",
                "context.event.resource_id": workspace.id,
            },
        )
        assert (
            len(events) == 1
            and events[0]["context"]["event"]["after"]["embedding_settings_revision"]
            == 1
        )


async def test_first_model_configuration_creation_serializes_revisions(
    postgres_raw_db, monkeypatch
):
    import asyncio

    from app.api.errors import ResourceConflictError
    from app.models.nodes import WorkspaceModelConfiguration
    from app.services.retrieval import embedding_configuration as service
    from tests.test_embedding_configuration import fixture

    monkeypatch.setattr(service, "overrides_supported", lambda: True)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        user, workspace, connection, body = await fixture()
        results = await asyncio.gather(
            service.save(user.user_id, workspace.id, body),
            service.save(user.user_id, workspace.id, body),
            return_exceptions=True,
        )
        assert sum(isinstance(value, dict) for value in results) == 1, results
        assert (
            sum(isinstance(value, ResourceConflictError) for value in results) == 1
        ), results
        rows = await postgres_raw_db.find(
            "node", {"entity": WorkspaceModelConfiguration._entity_name()}
        )
        assert len(rows) == 1 and rows[0]["context"]["embedding_settings_revision"] == 1
        edges = await postgres_raw_db.find(
            "edge", {"entity": "HasModelConfiguration", "source": workspace.id}
        )
        assert len(edges) == 1
