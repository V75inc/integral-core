"""Actual durable notification dispatch after current resource authority changes."""

from unittest.mock import AsyncMock

import pytest

from app.schemas.workspace_email import EmailConfiguration, SaveEmailDraft
from app.services import email_configuration as config
from app.services import email_delivery as delivery
from app.services.email_providers import DeliveryOutcome
from tests.test_email_delivery_authority import notice

pytestmark = [pytest.mark.postgres, pytest.mark.contract, pytest.mark.asyncio]


@pytest.fixture
async def queued_notice(monkeypatch):
    from app.agentive.services.work_items import work_item_object_id

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "00" * 32)
    values = await notice("guest")
    owner, _, workspace, *_ = values
    transport = AsyncMock(
        return_value=DeliveryOutcome("accepted", provider_message_id="provider-notice")
    )
    monkeypatch.setattr(delivery, "deliver", transport)
    draft = await config.save_draft(
        owner.id,
        workspace.id,
        SaveEmailDraft(
            expected_revision=0,
            configuration=EmailConfiguration(
                from_email="sender@example.com", api_key="synthetic"
            ),
        ),
    )
    await config.test_draft(
        owner.id,
        workspace.id,
        draft["revision"],
        requested_recipient="owner@example.com",
    )
    await config.activate(owner.id, workspace.id, draft["revision"])
    message = values[-1]
    result = await delivery.enqueue_email(message, idempotency_key="guest-notice")
    item_id = work_item_object_id(
        kind="email_dispatch",
        origin="workspace_email",
        principal_id=message.actor_user_id,
        workspace_id=workspace.id,
        idempotency_key=result.message_id,
    ).removeprefix("o.WorkItem.")
    transport.reset_mock()
    return values, result, item_id, transport


async def test_guest_notification_dispatches_through_real_worker(queued_notice):
    from app.agentive.services.work_worker import process_one_due_item

    _, result, item_id, transport = queued_notice
    item = await process_one_due_item(work_item_id=item_id)
    assert item.status == "succeeded"
    transport.assert_awaited_once()
    row = await delivery.database().get(
        "object", delivery.message_object(result.message_id)
    )
    assert (
        row["context"]["status"] == "accepted" and row["context"]["payload_enc"] == ""
    )


@pytest.mark.parametrize("revoke", ["grant", "notification", "membership"])
async def test_queued_notice_revocation_blocks_transport(queued_notice, revoke):
    from app.agentive.services.work_worker import process_one_due_item
    from app.models.edges import IS_MEMBER_OF

    values, result, item_id, transport = queued_notice
    _, recipient, workspace, _, _, _, grant, notification, _ = values
    if revoke == "grant":
        await grant.delete()
    elif revoke == "notification":
        await notification.delete(cascade=False)
    else:
        await recipient.disconnect(workspace, edge_type=IS_MEMBER_OF)
    await process_one_due_item(work_item_id=item_id)
    transport.assert_not_awaited()
    row = await delivery.database().get(
        "object", delivery.message_object(result.message_id)
    )
    assert row["context"]["status"] == "failed" and row["context"]["payload_enc"] == ""


@pytest.mark.parametrize("rollback", [False, True])
async def test_declared_email_effect_joins_real_operation_transaction(
    monkeypatch, rollback
):
    from app.schemas.policy import Subject
    from app.services.access_delegation import create_binding
    from app.services.app_operation_authority import operation_permit
    from app.services.app_operations.context import OperationContext
    from app.services.app_operations.transaction_scope import postgres_graph_transaction
    from app.services.application_definitions import definition_fingerprint
    from tests.test_app_action_delegation import fixture

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "00" * 32)
    monkeypatch.setattr(
        delivery, "deliver", AsyncMock(return_value=DeliveryOutcome("accepted"))
    )
    owner, author, workspace, app, _, entry, definition = await fixture()
    draft = await config.save_draft(
        owner.id,
        workspace.id,
        SaveEmailDraft(
            expected_revision=0,
            configuration=EmailConfiguration(
                from_email="sender@example.com", api_key="synthetic"
            ),
        ),
    )
    await config.test_draft(
        owner.id,
        workspace.id,
        draft["revision"],
        requested_recipient="owner@example.com",
    )
    await config.activate(owner.id, workspace.id, draft["revision"])
    definition.canonical_manifest["app"]["operations"][0]["authorization"][
        "effects"
    ] = ["email.enqueue"]
    definition.manifest_fingerprint = definition_fingerprint(
        definition.canonical_manifest
    )
    await definition.save()
    await create_binding(
        actor_id=owner.id,
        app_id=app.id,
        subject_kind="human",
        subject_id=author.id,
        role="reviewer",
        conditions=[],
    )
    ctx = OperationContext(
        user_id=author.id,
        workspace_id=workspace.id,
        app_id=app.id,
        scope=f"app:{app.id}",
        operation_key="approve",
        actor_kind="human",
        actor_id=author.id,
    )

    async def send():
        async with postgres_graph_transaction():
            async with operation_permit(
                principal_id=author.id,
                workspace_id=workspace.id,
                app_id=app.id,
                operation_key="approve",
                payload={"entry_id": entry.id},
                subject=Subject(kind="human", id=author.id),
            ):
                result = await ctx.enqueue_workspace_transactional_email(
                    to="recipient@example.com",
                    subject="Approved notice",
                    text="Notice",
                    html="<p>Notice</p>",
                    source_kind="app_notice",
                    idempotency_key="transaction-1",
                )
                if rollback:
                    raise RuntimeError("origin rolled back")
                return result

    if rollback:
        with pytest.raises(RuntimeError, match="origin rolled back"):
            await send()
    else:
        assert (await send())["status"] == "queued"
    rows = await delivery.database().find(
        "object", {"entity": "EmailMessage", "context.source_kind": "app_notice"}
    )
    assert len(rows) == (0 if rollback else 1)
    if not rollback:
        from app.agentive.services.work_items import work_item_object_id

        identity = rows[0]["context"]["message_id"]
        key = work_item_object_id(
            kind="email_dispatch",
            origin="workspace_email",
            principal_id=author.id,
            workspace_id=workspace.id,
            idempotency_key=identity,
        )
        assert await delivery.database().get("object", key)


async def test_revocation_after_worker_admission_still_blocks_transport(
    queued_notice, monkeypatch
):
    from app.agentive.services.work_worker import process_one_due_item

    values, result, item_id, transport = queued_notice
    grant = values[6]
    original = delivery._admit_source
    admitted = False

    async def revoke_after_first_check(message):
        nonlocal admitted
        await original(message)
        if not admitted:
            admitted = True
            await grant.delete()

    check = AsyncMock(side_effect=revoke_after_first_check)
    monkeypatch.setattr(delivery, "_admit_source", check)
    await process_one_due_item(work_item_id=item_id)
    assert check.await_count == 2
    transport.assert_not_awaited()
    row = await delivery.database().get(
        "object", delivery.message_object(result.message_id)
    )
    assert row["context"]["status"] == "failed" and row["context"]["payload_enc"] == ""
