"""Filing existing chat bytes must share one graph/approval receipt commit."""

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive import staging
from app.agentive.services import staging_apply, staging_execution
from app.agentive.staging import StagingError, create_staged_change
from app.models.edges import CATALOGS, HAS_ATTACHMENT
from app.models.nodes import Attachment, ChatThread
from app.services.agent_scope import current_scope_workspace_id
from app.services.chat_threads import create_thread
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def filing_proposal():
    auth, workspace, _, create = await proposal()
    created = await staging_apply.bless_and_execute(user_id=auth.id, token=create.token)
    assert created["consumed"], created
    entry_id = created["execute_result"]["entry"]["id"]
    thread = await create_thread(
        user_id=auth.id, provider_id="integral_native", workspace_id=workspace.id
    )
    attachment = await Attachment.create(
        filename="approved-note.txt",
        mime_type="text/plain",
        size=12,
        storage_key="chat/synthetic-filing-proof",
        content_hash="a" * 64,
        uploaded_by=auth.id,
        owner_kind="chat",
        scan_status="skipped",
    )
    await thread.connect(attachment, edge=HAS_ATTACHMENT)
    reset = current_scope_workspace_id.set(workspace.id)
    try:
        change = await create_staged_change(
            user_id=auth.id,
            session_id=None,
            kind="attach_uploaded_file",
            summary="File the uploaded note",
            diff_human="Attach the existing note to this Entry",
            diff_machine={},
            payload={"entry_id": entry_id, "attachment_id": attachment.id},
        )
    finally:
        current_scope_workspace_id.reset(reset)
    return auth, workspace, entry_id, attachment, change


async def test_filing_completion_loss_leaves_exact_applied_receipt(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "aa" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, entry_id, attachment, change = await filing_proposal()

        consume = staging_apply.consume_token

        async def unavailable(**kwargs):
            raise StagingError("completion_unavailable", "Lost filing completion")

        monkeypatch.setattr(staging_apply, "consume_token", unavailable)
        result = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        assert result["application_state"] == "reconciliation_required", result
        current = await postgres_raw_db.get("node", entry_id)
        assert attachment.id in current["context"]["attachment_ids"]
        receipt = await postgres_raw_db.get(
            "object", staging_execution.execution_record_id(change.token)
        )
        assert receipt is not None and receipt["context"]["state"] == "applied"
        filed = await postgres_raw_db.get("node", attachment.id)
        edges = await postgres_raw_db.find("edge", {}, sort=[("id", 1)])
        outboxes = await postgres_raw_db.find(
            "object", {"entity": "OperationEventOutbox"}
        )
        staging._tokens.clear()
        monkeypatch.setattr(staging_apply, "consume_token", consume)
        recovered = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        assert recovered["consumed"], recovered
        assert recovered["execute_result"]["execution_receipt"]["replayed"] is True
        assert await postgres_raw_db.get("node", entry_id) == current
        assert await postgres_raw_db.get("node", attachment.id) == filed
        assert await postgres_raw_db.find("edge", {}, sort=[("id", 1)]) == edges
        assert (
            await postgres_raw_db.find("object", {"entity": "OperationEventOutbox"})
            == outboxes
        )


async def test_filing_receipt_failure_rolls_back_ownership_entry_and_edges(
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

        async def unavailable(**kwargs):
            raise RuntimeError("Injected failed filing receipt")

        monkeypatch.setattr(
            staging_execution, "record_staging_execution_result", unavailable
        )
        result = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        assert result["consumed"] is False, result
        assert await postgres_raw_db.get("node", entry_id) == entry_before
        assert await postgres_raw_db.get("node", attachment.id) == file_before
        assert await postgres_raw_db.find("edge", {}, sort=[("id", 1)]) == edges_before

        assert (
            await postgres_raw_db.find("object", {"entity": "OperationEventOutbox"})
            == events_before
        )


@pytest.mark.parametrize(
    "corruption",
    ["filed", "blocked", "detached_catalog", "foreign_workspace", "ambiguous_parent"],
)
async def test_filing_rechecks_primary_source_and_structural_ownership(
    postgres_raw_db, monkeypatch, corruption
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "aa" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, entry_id, attachment, change = await filing_proposal()
        parents = await postgres_raw_db.find(
            "edge", {"entity": HAS_ATTACHMENT._entity_name(), "target": attachment.id}
        )
        assert len(parents) == 1
        thread_id = parents[0]["source"]
        if corruption in {"filed", "blocked"}:
            key, value = (
                ("owner_kind", "entry")
                if corruption == "filed"
                else ("scan_status", "blocked")
            )
            await postgres_raw_db.find_one_and_update(
                "node", {"id": attachment.id}, {"$set": {f"context.{key}": value}}
            )
        elif corruption == "detached_catalog":
            owners = await postgres_raw_db.find(
                "edge", {"entity": CATALOGS._entity_name(), "target": thread_id}
            )
            assert len(owners) == 1
            await postgres_raw_db.delete("edge", owners[0]["id"])
        elif corruption == "foreign_workspace":
            await postgres_raw_db.find_one_and_update(
                "node",
                {"id": thread_id},
                {"$set": {"context.workspace_id": "n.Workspace.foreign"}},
            )
        else:
            other = await create_thread(
                user_id=auth.id,
                provider_id="integral_native",
                workspace_id=(await ChatThread.get(thread_id)).workspace_id,
            )
            await other.connect(
                await Attachment.get(attachment.id), edge=HAS_ATTACHMENT
            )
        entry_before = await postgres_raw_db.get("node", entry_id)
        file_before = await postgres_raw_db.get("node", attachment.id)
        edges_before = await postgres_raw_db.find("edge", {}, sort=[("id", 1)])
        result = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        assert not result["consumed"], result
        assert result["execute_result"]["error"], result
        assert await postgres_raw_db.get("node", entry_id) == entry_before
        assert await postgres_raw_db.get("node", attachment.id) == file_before
        assert await postgres_raw_db.find("edge", {}, sort=[("id", 1)]) == edges_before


async def test_concurrent_filings_cannot_give_one_chat_file_two_entry_homes(
    postgres_raw_db, monkeypatch
):
    """Two pools contend on the source, even when the Entry targets differ."""
    import asyncio
    from contextvars import Context

    from jvspatial.db.factory import create_database

    from app.models.nodes import Entry

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "aa" * 32)
    second = create_database(db_type="postgres", min_size=1, max_size=1)
    tasks = []
    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            auth, workspace, entry_id, attachment, first = await filing_proposal()
            entry = await Entry.get(entry_id)
            reset = current_scope_workspace_id.set(workspace.id)
            try:
                create = await create_staged_change(
                    user_id=auth.id,
                    session_id=None,
                    kind="create_entry",
                    summary="Create a second target",
                    diff_human="A second target",
                    diff_machine={},
                    payload={"track_id": entry.track_id, "title": "Second target"},
                )
                created = await staging_apply.bless_and_execute(
                    user_id=auth.id, token=create.token
                )
                assert created["consumed"], created
                other_id = created["execute_result"]["entry"]["id"]
                other = await create_staged_change(
                    user_id=auth.id,
                    session_id=None,
                    kind="attach_uploaded_file",
                    summary="File to the other target",
                    diff_human="File existing bytes",
                    diff_machine={},
                    payload={"entry_id": other_id, "attachment_id": attachment.id},
                )
            finally:
                current_scope_workspace_id.reset(reset)
            for collection in ("node", "edge", "object"):
                await second.find(collection, {"id": ""}, limit=1)

            async def apply(database, change):
                async with scoped_default_context_async(
                    GraphContext(database=database)
                ):
                    return await staging_apply.bless_and_execute(
                        user_id=auth.id, token=change.token
                    )

            tasks = [
                asyncio.create_task(apply(db, change), context=Context())
                for db, change in ((postgres_raw_db, first), (second, other))
            ]
            results = await asyncio.wait_for(asyncio.gather(*tasks), 30)
            assert sum(bool(result.get("consumed")) for result in results) == 1, results
            rows = [
                await postgres_raw_db.get("node", target)
                for target in (entry_id, other_id)
            ]
            assert (
                sum(attachment.id in row["context"]["attachment_ids"] for row in rows)
                == 1
            )
            assert sorted(row["context"]["record_revision"] for row in rows) == [1, 2]
            edges = await postgres_raw_db.find(
                "edge",
                {"entity": HAS_ATTACHMENT._entity_name(), "target": attachment.id},
            )
            assert len(edges) == 2
            assert sum(edge["source"] in (entry_id, other_id) for edge in edges) == 1
            receipts = [
                await postgres_raw_db.get(
                    "object", staging_execution.execution_record_id(change.token)
                )
                for change in (first, other)
            ]
            assert (
                sum(
                    bool(record and record["context"]["state"] == "applied")
                    for record in receipts
                )
                == 1
            )
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await second.close()


async def test_cancellation_after_filing_commit_recovers_without_refiling(
    postgres_raw_db, monkeypatch
):
    """A disconnected caller cannot turn committed ownership into a fresh write."""
    import asyncio

    from app.services.app_operations import event_outbox
    from app.services.app_operations.transaction_scope import (
        OperationTransactionUnavailable,
        current_graph_transaction_database,
    )

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "aa" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, entry_id, attachment, change = await filing_proposal()
        entered = asyncio.Event()
        delivery = event_outbox.deliver_operation_event

        async def blocked(**kwargs):
            with pytest.raises(OperationTransactionUnavailable):
                current_graph_transaction_database()
            receipt = await postgres_raw_db.get(
                "object", staging_execution.execution_record_id(change.token)
            )
            assert receipt["context"]["state"] == "applied"
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(event_outbox, "deliver_operation_event", blocked)
        applying = asyncio.create_task(
            staging_apply.bless_and_execute(user_id=auth.id, token=change.token)
        )
        try:
            await asyncio.wait_for(entered.wait(), 15)
        finally:
            applying.cancel()
            with pytest.raises(asyncio.CancelledError):
                await applying
        before = await postgres_raw_db.get("node", entry_id)
        file_before = await postgres_raw_db.get("node", attachment.id)
        edges = await postgres_raw_db.find("edge", {}, sort=[("id", 1)])
        staging._tokens.clear()
        monkeypatch.setattr(event_outbox, "deliver_operation_event", delivery)
        recovered = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        assert recovered["consumed"], recovered
        assert recovered["execute_result"]["execution_receipt"]["replayed"]
        assert await postgres_raw_db.get("node", entry_id) == before
        assert await postgres_raw_db.get("node", attachment.id) == file_before
        assert await postgres_raw_db.find("edge", {}, sort=[("id", 1)]) == edges


@pytest.mark.parametrize("after_commit", [False, True])
async def test_filing_rechecks_revoked_authority_for_execution_and_recovery(
    postgres_raw_db, monkeypatch, after_commit
):
    """Approval/receipt history never supplies current workspace read or write rights."""
    from tests.contract.test_release_admission_postgres import remove_membership

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "aa" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, entry_id, attachment, change = await filing_proposal()
        consume = staging_apply.consume_token
        if after_commit:

            async def unavailable(**kwargs):
                raise StagingError("completion_unavailable", "Lost completion")

            monkeypatch.setattr(staging_apply, "consume_token", unavailable)
            first = await staging_apply.bless_and_execute(
                user_id=auth.id, token=change.token
            )
            assert first["application_state"] == "reconciliation_required", first
            monkeypatch.setattr(staging_apply, "consume_token", consume)
        await remove_membership(auth, workspace)
        before = await postgres_raw_db.get("node", entry_id)
        file_before = await postgres_raw_db.get("node", attachment.id)
        edges = await postgres_raw_db.find("edge", {}, sort=[("id", 1)])
        staging._tokens.clear()
        result = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        if after_commit:
            assert result["consumed"], result
            observed = result["execute_result"]
            assert observed["result_access"] == "withheld", observed
            assert "attachment" not in observed and "entry" not in observed
        else:
            assert not result["consumed"], result
            assert result["execute_result"]["error"], result
        assert await postgres_raw_db.get("node", entry_id) == before
        assert await postgres_raw_db.get("node", attachment.id) == file_before
        assert await postgres_raw_db.find("edge", {}, sort=[("id", 1)]) == edges
