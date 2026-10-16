"""Caller loss before an attachment receipt commits rolls back its whole filing."""

import asyncio

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive import staging
from app.agentive.services import staging_apply, staging_execution
from tests.contract.test_staging_attachment_filing_postgres import filing_proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def test_cancellation_before_receipt_commit_rolls_back_filing(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "aa" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, entry_id, attachment, change = await filing_proposal()
        entry_before = await postgres_raw_db.get("node", entry_id)
        file_before = await postgres_raw_db.get("node", attachment.id)
        edges_before = await postgres_raw_db.find("edge", {}, sort=[("id", 1)])
        events_before = await postgres_raw_db.find(
            "object", {"entity": "OperationEventOutbox"}
        )
        record = staging_execution.record_staging_execution_result
        entered = asyncio.Event()

        async def blocked(**kwargs):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(
            staging_execution, "record_staging_execution_result", blocked
        )
        applying = asyncio.create_task(
            staging_apply.bless_and_execute(user_id=auth.id, token=change.token)
        )
        try:
            await asyncio.wait_for(entered.wait(), 15)
        finally:
            applying.cancel()
            with pytest.raises(asyncio.CancelledError):
                await applying
        assert await postgres_raw_db.get("node", entry_id) == entry_before
        assert await postgres_raw_db.get("node", attachment.id) == file_before
        assert await postgres_raw_db.find("edge", {}, sort=[("id", 1)]) == edges_before
        assert (
            await postgres_raw_db.find("object", {"entity": "OperationEventOutbox"})
            == events_before
        )
        monkeypatch.setattr(
            staging_execution, "record_staging_execution_result", record
        )
        staging._tokens.clear()
        retried = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        assert retried["consumed"], retried
        current = await postgres_raw_db.get("node", entry_id)
        assert (
            current["context"]["record_revision"]
            == entry_before["context"]["record_revision"] + 1
        )
        assert current["context"]["attachment_ids"] == [attachment.id]
