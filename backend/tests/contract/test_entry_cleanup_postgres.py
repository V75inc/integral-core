"""Deletion rollback retains staged files; committed cleanup is retryable."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.models.edges import HAS_UPLOAD_SESSION
from app.models.nodes import Entry, UploadSession
from app.services.entry_cleanup_outbox import ENTITY, deliver_pending_cleanup
from app.services.entry_create import create_entry_in_track
from app.services.entry_deletion import delete_entry_fast
from tests.contract.test_entry_effect_ordering import typed_graph

pytestmark = [pytest.mark.postgres, pytest.mark.contract, pytest.mark.asyncio]


async def test_delete_rollback_retains_upload_then_postcommit_cleanup_retries(
    postgres_raw_db, tmp_path, monkeypatch
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, _, track, entry_type = await typed_graph()
        entry = await create_entry_in_track(
            track=track,
            user_id=owner.id,
            type_id=entry_type.id,
            title="Cleanup rollback",
        )
        session = await UploadSession.create(
            entry_id=entry.id, uploaded_by=owner.id, filename="proof.txt"
        )
        await entry.connect(session, edge=HAS_UPLOAD_SESSION)
        staged = tmp_path / session.id
        staged.mkdir()
        (staged / "chunk").write_bytes(b"preserve until commit")
        monkeypatch.setattr(
            "app.services.chunked_upload._session_dir",
            lambda identity: str(tmp_path / identity),
        )
        original = Entry.delete

        async def fail_final_delete(self, *args, **kwargs):
            if self.id == entry.id:
                raise RuntimeError("Injected final Entry delete failure")
            return await original(self, *args, **kwargs)

        with monkeypatch.context() as injection:
            injection.setattr(Entry, "delete", fail_final_delete)
            with pytest.raises(RuntimeError, match="Injected final"):
                await delete_entry_fast(entry, actor_user_id=owner.id)
        assert await postgres_raw_db.get("node", entry.id) is not None
        assert await postgres_raw_db.get("node", session.id) is not None
        assert (staged / "chunk").read_bytes() == b"preserve until commit"
        assert (
            await postgres_raw_db.find(
                "object", {"entity": ENTITY, "context.entry_id": entry.id}
            )
            == []
        )
        await delete_entry_fast(entry, actor_user_id=owner.id)
        assert await postgres_raw_db.get("node", entry.id) is None
        assert await postgres_raw_db.get("node", session.id) is None
        assert staged.exists()
        embedding = SimpleNamespace(
            soft_delete=AsyncMock(side_effect=RuntimeError("storage unavailable"))
        )
        monkeypatch.setattr(
            "app.services.retrieval.get_embedding_store", lambda: embedding
        )
        await deliver_pending_cleanup(database=postgres_raw_db)
        facts = await postgres_raw_db.find(
            "object", {"entity": ENTITY, "context.entry_id": entry.id}
        )
        assert facts[0]["context"]["status"] == "pending"
        embedding.soft_delete.side_effect = None
        await deliver_pending_cleanup(database=postgres_raw_db)
        facts = await postgres_raw_db.find(
            "object", {"entity": ENTITY, "context.entry_id": entry.id}
        )
        assert facts[0]["context"]["status"] == "delivered"
        assert not staged.exists()
