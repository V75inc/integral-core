"""Reviewed chat bytes stay bound to their approved Entry effect."""

from unittest.mock import AsyncMock

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async
from jvspatial.storage import create_storage

from app.agentive import artifacts
from app.agentive.services import staging_apply
from app.agentive.staging import create_staged_change
from app.agentive.tooling import bindings
from app.agentive.tooling.dispatch import dispatch_tool
from app.models.edges import HAS_ATTACHMENT
from app.services import attachment_storage, chat_attachment_review
from app.services.agent_scope import current_scope_workspace_id
from app.services.chat_threads import create_thread
from app.services.document_draft_access import load_document_draft_snapshot
from app.services.document_drafts import render_document_draft
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


@pytest.mark.parametrize("cancel_during_write", [False, True])
async def test_worker_broker_preparation_fences_publication(
    postgres_raw_db, monkeypatch, tmp_path, cancel_during_write
):
    """The real worker adapter performs I/O outside, then rechecks its lease."""
    from datetime import datetime, timezone

    from app.agentive.services import capability_broker, work_items
    from app.agentive.services.capability_adapters import AdapterError
    from app.agentive.services.work_execution import build_work_execution_context
    from app.agentive.services.work_outbox import cas_work_item_update
    from app.schemas.capability_broker import CapabilityInvocation

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    storage = attachment_storage.AttachmentStorageService(
        create_storage(
            "local",
            root_dir=str(tmp_path),
            allowed_mime_types=attachment_storage._storage_allowed_mime_types(),
            create_root=True,
        )
    )
    monkeypatch.setattr(attachment_storage, "_ATTACHMENT_STORAGE_SERVICE", storage)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, create = await proposal()
        created = await staging_apply.bless_and_execute(
            user_id=auth.id, token=create.token
        )
        entry_id = created["execute_result"]["entry"]["id"]
        thread = await create_thread(
            user_id=auth.id, provider_id="integral_native", workspace_id=workspace.id
        )
        await artifacts.upsert_artifact(
            user_id=auth.id,
            workspace_id=workspace.id,
            session_id=thread.id,
            key="report",
            kind="document_draft",
            title="Report",
            body="Reviewed text.",
            metadata={"format": "markdown"},
            expected_version=0,
        )
        queued = await work_items.enqueue_work_item(
            kind="capability",
            origin="test",
            principal_id=auth.id,
            workspace_id=workspace.id,
            thread_id=thread.id,
            idempotency_key=f"draft-worker-{thread.id}",
            input_payload={"capability_key": "integral_prepare_document_attachment"},
        )
        claimed = await work_items.claim_due_candidate(
            worker_id="draft-test", lease_seconds=60, work_item_id=queued.work_item_id
        )
        context = build_work_execution_context(
            work_item=claimed, logical_step_key="native-harness:0"
        )
        invocation = CapabilityInvocation(
            run_id=context.run_id,
            principal_id=auth.id,
            workspace_id=workspace.id,
            session_id=thread.id,
            origin="chat",
            source="core",
            op_class="propose",
            capability_key="integral_prepare_document_attachment",
            arguments={"key": "report", "version": 1},
            work_execution_context=context,
        )
        if cancel_during_write:
            original_save = storage.save_attachment

            async def save_then_cancel(**kwargs):
                result = await original_save(**kwargs)
                await cas_work_item_update(
                    claimed.work_item_id,
                    expected={"status": "running", "lease_fence": claimed.lease_fence},
                    updates={
                        "cancel_requested_at": datetime.now(timezone.utc).isoformat()
                    },
                )
                return result

            monkeypatch.setattr(storage, "save_attachment", save_then_cancel)
            with pytest.raises(AdapterError, match="work.cancelled"):
                await capability_broker._call_adapter(invocation, {})
            assert (
                await thread.count_nodes(
                    edge=[HAS_ATTACHMENT], node=["Attachment"], direction="out"
                )
                == 0
            )
        else:
            result = await capability_broker._call_adapter(invocation, {})
            assert result["attachment_id"]
            assert (
                await thread.count_nodes(
                    edge=[HAS_ATTACHMENT], node=["Attachment"], direction="out"
                )
                == 1
            )
            attach_context = build_work_execution_context(
                work_item=claimed, logical_step_key="native-harness:1"
            )
            attach = invocation.model_copy(
                update={
                    "capability_key": "integral_attach_uploaded_file_to_entry",
                    "arguments": {
                        "entry_id": entry_id,
                        "attachment_id": result["attachment_id"],
                    },
                    "work_execution_context": attach_context,
                }
            )
            original_inspect = chat_attachment_review.inspect_chat_attachment_for_review
            original_row = await postgres_raw_db.get("node", result["attachment_id"])
            stored_key = original_row["context"]["storage_key"]

            async def change_after_inspection(**kwargs):
                inspected = await original_inspect(**kwargs)
                await postgres_raw_db.find_one_and_update(
                    "node",
                    {"id": result["attachment_id"]},
                    {"$set": {"context.storage_key": "changed-after-review"}},
                )
                return inspected

            monkeypatch.setattr(
                chat_attachment_review,
                "inspect_chat_attachment_for_review",
                change_after_inspection,
            )
            with pytest.raises(AdapterError):
                await capability_broker._call_adapter(attach, {})
            monkeypatch.setattr(
                chat_attachment_review,
                "inspect_chat_attachment_for_review",
                original_inspect,
            )
            attachment = await postgres_raw_db.get("node", result["attachment_id"])
            assert attachment["context"]["storage_key"] == "changed-after-review"
            await postgres_raw_db.find_one_and_update(
                "node",
                {"id": result["attachment_id"]},
                {"$set": {"context.storage_key": stored_key}},
            )
            proposal_result = await capability_broker._call_adapter(attach, {})
            assert proposal_result["_kind"] == "staged_change"


async def test_approved_file_refuses_changed_bytes_and_source_then_applies_once(
    postgres_raw_db, monkeypatch, tmp_path
):
    """The exact reviewed file survives preflight, effect and receipt retry."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    storage = attachment_storage.AttachmentStorageService(
        create_storage(
            "local",
            root_dir=str(tmp_path),
            allowed_mime_types=attachment_storage._storage_allowed_mime_types(),
            create_root=True,
        )
    )
    monkeypatch.setattr(attachment_storage, "_ATTACHMENT_STORAGE_SERVICE", storage)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, create = await proposal()
        created = await staging_apply.bless_and_execute(
            user_id=auth.id, token=create.token
        )
        assert created["consumed"], created
        entry_id = created["execute_result"]["entry"]["id"]
        thread = await create_thread(
            user_id=auth.id, provider_id="integral_native", workspace_id=workspace.id
        )
        await artifacts.upsert_artifact(
            user_id=auth.id,
            workspace_id=workspace.id,
            session_id=thread.id,
            key="report",
            kind="document_draft",
            title="Report",
            body="Approved content for the report.",
            metadata={"format": "markdown"},
            expected_version=0,
        )
        snapshot = await load_document_draft_snapshot(
            user_id=auth.id,
            workspace_id=workspace.id,
            thread_id=thread.id,
            key="report",
            version=1,
        )
        content = render_document_draft(
            title=snapshot.title, body=snapshot.body, format=snapshot.format
        ).content
        arguments = {"key": "report", "version": 1}
        result = await dispatch_tool(
            "integral_prepare_document_attachment",
            arguments,
            principal_id=auth.id,
            scope=workspace.id,
            session_id=thread.id,
        )
        assert not result.is_error, result
        attachment_id = result.data["attachment_id"]
        repeated = await dispatch_tool(
            "integral_prepare_document_attachment",
            arguments,
            principal_id=auth.id,
            scope=workspace.id,
            session_id=thread.id,
        )
        assert repeated.data["attachment_id"] == attachment_id
        for principal, scope_id, conversation, data in (
            (auth.id, "", thread.id, arguments),
            ("o.User.foreign", workspace.id, thread.id, arguments),
            (auth.id, workspace.id, thread.id, {"key": "report", "version": 2}),
        ):
            denied = await dispatch_tool(
                "integral_prepare_document_attachment",
                data,
                principal_id=principal,
                scope=scope_id,
                session_id=conversation,
            )
            assert denied.is_error
        outboxes = await postgres_raw_db.find(
            "object",
            {
                "entity": "OperationEventOutbox",
                "context.event.resource_id": attachment_id,
            },
        )
        assert len(outboxes) == 1
        assert outboxes[0]["context"]["event"]["action"] == "attachment.create"
        principal = bindings._propose_principal.set(auth.id)
        session = bindings._propose_session_id.set(thread.id)
        scope = current_scope_workspace_id.set(workspace.id)
        try:
            staged = await bindings._stage_attach_uploaded_file(
                {"entry_id": entry_id, "attachment_id": attachment_id}
            )
            change = await create_staged_change(
                user_id=auth.id, session_id=thread.id, **staged
            )
        finally:
            current_scope_workspace_id.reset(scope)
            bindings._propose_session_id.reset(session)
            bindings._propose_principal.reset(principal)
        assert staged["payload"]["expected_size"] == len(content)
        entry_before = await postgres_raw_db.get("node", entry_id)
        file_before = await postgres_raw_db.get("node", attachment_id)

        original_read = storage.read_attachment
        monkeypatch.setattr(
            storage, "read_attachment", AsyncMock(return_value=b"altered")
        )
        refused = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        assert refused["consumed"] is False, refused
        assert await postgres_raw_db.get("node", entry_id) == entry_before
        assert await postgres_raw_db.get("node", attachment_id) == file_before
        monkeypatch.setattr(storage, "read_attachment", original_read)

        original_inspect = chat_attachment_review.inspect_chat_attachment_for_review

        async def swap_source(**kwargs):
            inspected = await original_inspect(**kwargs)
            await postgres_raw_db.find_one_and_update(
                "node",
                {"id": attachment_id},
                {"$set": {"context.storage_key": "different-key"}},
            )
            return inspected

        monkeypatch.setattr(
            chat_attachment_review, "inspect_chat_attachment_for_review", swap_source
        )
        refused = await staging_apply.execute_blessed_change(
            user_id=auth.id, token=change.token
        )
        assert refused["consumed"] is False, refused
        assert await postgres_raw_db.get("node", entry_id) == entry_before
        monkeypatch.setattr(
            chat_attachment_review,
            "inspect_chat_attachment_for_review",
            original_inspect,
        )
        await postgres_raw_db.find_one_and_update(
            "node",
            {"id": attachment_id},
            {"$set": {"context.storage_key": file_before["context"]["storage_key"]}},
        )

        applied = await staging_apply.execute_blessed_change(
            user_id=auth.id, token=change.token
        )
        assert applied["consumed"], applied
        assert (
            attachment_id
            in (await postgres_raw_db.get("node", entry_id))["context"][
                "attachment_ids"
            ]
        )
        assert (await postgres_raw_db.get("node", attachment_id))["context"][
            "owner_kind"
        ] == "entry"
        edges = await postgres_raw_db.find(
            "edge",
            {
                "entity": HAS_ATTACHMENT._entity_name(),
                "source": entry_id,
                "target": attachment_id,
            },
        )
        assert len(edges) == 1
