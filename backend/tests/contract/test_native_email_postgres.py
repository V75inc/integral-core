"""PostgreSQL configuration, transactional intent and recovery contracts."""

import json
from unittest.mock import AsyncMock

import pytest

from app.api.errors import BadRequestError
from app.schemas.workspace_email import EmailConfiguration, SaveEmailDraft
from app.services import email_configuration as config
from app.services import email_delivery as delivery
from app.services.email_providers import DeliveryOutcome
from app.services.email_service import EmailMessage
from app.services.personal_workspace import ensure_personal_workspace

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


@pytest.fixture
async def configured(test_user, monkeypatch):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "00" * 32)
    workspace = await ensure_personal_workspace(test_user)
    workspace_id = workspace.id if hasattr(workspace, "id") else workspace
    draft = await config.save_draft(
        test_user.id,
        workspace_id,
        SaveEmailDraft(
            expected_revision=0,
            configuration=EmailConfiguration(
                from_email="sender@example.com", api_key="synthetic-key"
            ),
        ),
    )
    monkeypatch.setattr(
        delivery,
        "deliver",
        AsyncMock(
            return_value=DeliveryOutcome("accepted", provider_message_id="provider-id")
        ),
    )
    await config.test_draft(test_user.id, workspace_id, draft["revision"])
    active = await config.activate(test_user.id, workspace_id, draft["revision"])
    return test_user.id, workspace_id, active


async def queued(configured, key):
    actor, workspace, _ = configured
    message = EmailMessage(
        to="recipient@example.com",
        subject="Test",
        html="<p>Hi</p>",
        text="Hi",
        workspace_id=workspace,
        actor_user_id=actor,
        source_kind="app_message",
    )
    result = await delivery.enqueue_email(message, idempotency_key=key)
    from app.agentive.services import work_items

    item_id = work_items.work_item_object_id(
        kind="email_dispatch",
        origin="workspace_email",
        principal_id=actor,
        workspace_id=workspace,
        idempotency_key=result.message_id,
    ).removeprefix("o.WorkItem.")
    return message, result, item_id


async def test_revision_and_activation(configured):
    actor, workspace, active = configured
    assert active["mode"] == "custom" and active["active"]["api_key_configured"]
    assert "synthetic-key" not in json.dumps(active)
    with pytest.raises(BadRequestError):
        await config.activate(actor, workspace, active["revision"])
    with pytest.raises(BadRequestError):
        await config.save_draft(
            actor,
            workspace,
            SaveEmailDraft(expected_revision=0, configuration=EmailConfiguration()),
        )


async def test_enqueue_deduplication(configured):
    message, first, _ = await queued(configured, "same-command")
    assert first == await delivery.enqueue_email(
        message, idempotency_key="same-command"
    )
    saved = await delivery.database().get(
        "object", delivery.message_object(first.message_id)
    )
    assert saved["context"]["payload_enc"].startswith("v1:")
    assert "<p>Hi</p>" not in json.dumps(saved)
    message.subject = "Changed"
    with pytest.raises(BadRequestError):
        await delivery.enqueue_email(message, idempotency_key="same-command")


async def test_worker_acceptance_and_unknown_recovery(configured):
    from app.agentive.services import work_worker

    _, result, item_id = await queued(configured, "worker-command")
    item = await work_worker.process_one_due_item(work_item_id=item_id)
    assert item.status == "succeeded"
    saved = await delivery.database().get(
        "object", delivery.message_object(result.message_id)
    )
    assert (
        saved["context"]["status"] == "accepted"
        and saved["context"]["payload_enc"] == ""
    )
    _, interrupted, item_id = await queued(configured, "interrupted-command")
    await delivery.database().find_one_and_update(
        "object",
        {"id": delivery.message_object(interrupted.message_id)},
        {"$set": {"context.status": "dispatching"}},
    )
    before = delivery.deliver.await_count
    await work_worker.process_one_due_item(work_item_id=item_id)
    assert delivery.deliver.await_count == before
    saved = await delivery.database().get(
        "object", delivery.message_object(interrupted.message_id)
    )
    assert saved["context"]["status"] == "unknown"


async def test_provider_replacement_blocks_pending(configured):
    from app.agentive.services import work_worker

    _, result, item_id = await queued(configured, "pending-command")
    actor, workspace, active = configured
    await config.activate(actor, workspace, active["revision"], disable=True)
    before = delivery.deliver.await_count
    await work_worker.process_one_due_item(work_item_id=item_id)
    assert delivery.deliver.await_count == before
    saved = await delivery.database().get(
        "object", delivery.message_object(result.message_id)
    )
    assert saved["context"]["status"] == "failed"


async def test_enqueue_rolls_back_with_originating_transaction(configured):
    from app.services.app_operations.transaction_scope import postgres_graph_transaction

    result = None
    with pytest.raises(RuntimeError):
        async with postgres_graph_transaction():
            _, result, _ = await queued(configured, "rolled-back")
            raise RuntimeError("originating mutation failed")
    assert (
        await delivery.database().get(
            "object", delivery.message_object(result.message_id)
        )
        is None
    )


async def test_simultaneous_same_command_creates_one_intent(configured):
    import asyncio

    from app.agentive.work_models import WorkItem

    first, second = await asyncio.gather(
        queued(configured, "concurrent"), queued(configured, "concurrent")
    )
    assert first[1].message_id == second[1].message_id
    items = await WorkItem.find(
        {
            "context.kind": "email_dispatch",
            "context.idempotency_key": first[1].message_id,
        }
    )
    assert len(items) == 1


async def test_competing_workers_dispatch_once(configured):
    import asyncio

    from app.agentive.services import work_worker

    _, result, item_id = await queued(configured, "competing")
    before = delivery.deliver.await_count
    await asyncio.gather(
        work_worker.process_one_due_item(work_item_id=item_id, worker_id="first"),
        work_worker.process_one_due_item(work_item_id=item_id, worker_id="second"),
    )
    assert delivery.deliver.await_count == before + 1
    saved = await delivery.database().get(
        "object", delivery.message_object(result.message_id)
    )
    assert saved["context"]["status"] == "accepted"


async def test_definitive_rejection_retries_but_read_timeout_does_not(
    configured, monkeypatch
):
    from app.agentive.services import work_worker

    _, result, item_id = await queued(configured, "throttled")
    monkeypatch.setattr(
        delivery,
        "deliver",
        AsyncMock(
            return_value=DeliveryOutcome(
                "failed", "Throttled", retryable=True, retry_after=120
            )
        ),
    )
    item = await work_worker.process_one_due_item(work_item_id=item_id)
    assert item.status == "retry_wait"
    saved = await delivery.database().get(
        "object", delivery.message_object(result.message_id)
    )
    assert saved["context"]["status"] == "queued" and saved["context"]["payload_enc"]
    _, result, item_id = await queued(configured, "uncertain")
    monkeypatch.setattr(
        delivery,
        "deliver",
        AsyncMock(return_value=DeliveryOutcome("unknown", "Outcome unknown")),
    )
    item = await work_worker.process_one_due_item(work_item_id=item_id)
    assert item.status == "failed"
    saved = await delivery.database().get(
        "object", delivery.message_object(result.message_id)
    )
    assert (
        saved["context"]["status"] == "unknown" and not saved["context"]["payload_enc"]
    )


async def test_current_membership_required_and_guests_cannot_configure(
    configured, test_user2
):
    from app.api.errors import InsufficientPermissionsError
    from app.models.edges import IS_MEMBER_OF

    actor, workspace, active = configured
    from app.models.nodes import Workspace

    node = await Workspace.get(workspace)
    await test_user2.connect(node, edge=IS_MEMBER_OF, role="guest")
    with pytest.raises(InsufficientPermissionsError):
        await config.describe(test_user2.id, workspace)
    message = EmailMessage(
        to="recipient@example.com",
        subject="Test",
        html="",
        text="",
        workspace_id=workspace,
        actor_user_id=test_user2.id,
        source_kind="app_message",
    )
    with pytest.raises(InsufficientPermissionsError):
        await delivery.enqueue_email(message, idempotency_key="guest-denied")


async def test_migration_preserves_sender_and_removes_plaintext(monkeypatch):
    from app.services.email_migration import migrate_workspace
    from tests.test_resource_location import graph

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "00" * 32)
    actor, workspace, app, _, _ = await graph()
    app.installed_package_slug = "email-log"
    app.settings = {
        "delivery_enabled": True,
        "delivery_provider": "sendgrid",
        "from_email": "legacy@example.com",
        "from_name": "Legacy",
        "api_key": "synthetic-legacy",
    }
    await app.save()
    inspection = await migrate_workspace(actor.id, workspace.id)
    assert inspection["status"] == "ready"
    assert await config.record(workspace.id) is None
    result = await migrate_workspace(actor.id, workspace.id, apply=True)
    assert result["status"] == "migrated"
    loaded = await config.record(workspace.id)
    assert config.unseal(loaded.active, workspace.id).api_key == "synthetic-legacy"
    assert loaded.active["configuration"]["from_email"] == "legacy@example.com"
    from app.models.nodes import App

    app = await App.get(app.id)
    assert "api_key" not in app.settings and "api_key_enc" not in app.settings
    assert (await migrate_workspace(actor.id, workspace.id, apply=True))[
        "status"
    ] == "already_configured"


async def test_draft_edits_preserve_secrets_and_require_a_new_test(configured):
    actor, workspace, active = configured
    saved = await config.save_draft(
        actor,
        workspace,
        SaveEmailDraft(
            expected_revision=active["revision"],
            configuration=EmailConfiguration(
                from_email="sender@example.com", from_name="Renamed"
            ),
        ),
    )
    node = await config.record(workspace)
    assert config.unseal(node.draft, workspace).api_key == "synthetic-key"
    assert node.active["connection_id"] != node.draft["connection_id"]
    assert saved["tested_revision"] is None
    with pytest.raises(BadRequestError):
        await config.activate(actor, workspace, saved["revision"])
    await config.test_draft(actor, workspace, saved["revision"])
    updated = await config.activate(actor, workspace, saved["revision"])
    assert updated["active"]["from_name"] == "Renamed"
    old = await delivery.database().get(
        "object", "o.EmailConnection." + active["active"]["connection_id"]
    )
    assert old["context"]["retired_at"]


async def test_missing_key_can_be_replaced_without_decrypting_it(configured):
    actor, workspace, active = configured
    node = await config.record(workspace)
    broken = {
        **node.draft,
        "configuration": {**node.draft["configuration"], "api_key": "v1:broken"},
    }
    await delivery.database().find_one_and_update(
        "node", {"id": node.id}, {"$set": {"context.draft": broken}}
    )
    with pytest.raises(BadRequestError):
        await config.save_draft(
            actor,
            workspace,
            SaveEmailDraft(
                expected_revision=active["revision"],
                configuration=EmailConfiguration(from_email="sender@example.com"),
            ),
        )
    await config.save_draft(
        actor,
        workspace,
        SaveEmailDraft(
            expected_revision=active["revision"],
            configuration=EmailConfiguration(
                from_email="sender@example.com", api_key="replacement"
            ),
        ),
    )
    assert (
        config.unseal((await config.record(workspace)).draft, workspace).api_key
        == "replacement"
    )


async def test_history_pagination_and_platform_configuration_binding(
    configured, monkeypatch
):
    from app.agentive.services import work_worker
    from app.config import settings

    actor, workspace, active = configured
    await queued(configured, "page-one")
    await queued(configured, "page-two")
    first = await delivery.history(actor, workspace, limit=1)
    second = await delivery.history(
        actor, workspace, limit=1, cursor=first["next_cursor"]
    )
    assert first["items"][0]["message_id"] != second["items"][0]["message_id"]
    assert "payload_enc" not in json.dumps(first)
    await config.activate(actor, workspace, active["revision"], disable=True)
    monkeypatch.setattr(settings, "EMAIL_PROVIDER", "resend")
    monkeypatch.setattr(settings, "RESEND_API_KEY", "synthetic-platform")
    _, result, item_id = await queued(configured, "platform-bound")
    monkeypatch.setattr(settings, "RESEND_API_KEY", "rotated-platform")
    before = delivery.deliver.await_count
    await work_worker.process_one_due_item(work_item_id=item_id)
    assert delivery.deliver.await_count == before
    row = await delivery.database().get(
        "object", delivery.message_object(result.message_id)
    )
    assert row["context"]["status"] == "failed"


async def test_migration_refuses_missing_credentials_and_existing_drafts(monkeypatch):
    from app.services.email_migration import migrate_workspace
    from tests.test_resource_location import graph

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "00" * 32)
    actor, workspace, app, _, _ = await graph()
    assert (await migrate_workspace(actor.id, workspace.id))["status"] == "absent"
    app.installed_package_slug = "email-log"
    await app.save()
    assert (await migrate_workspace(actor.id, workspace.id))[
        "status"
    ] == "no_credentials"
    app.settings = {"api_key_enc": "v1:broken", "from_email": "sender@example.com"}
    await app.save()
    with pytest.raises(BadRequestError):
        await migrate_workspace(actor.id, workspace.id, apply=True)
    app.settings = {"api_key": "legacy", "from_email": "sender@example.com"}
    await app.save()
    await config.save_draft(
        actor.id,
        workspace.id,
        SaveEmailDraft(
            expected_revision=0,
            configuration=EmailConfiguration(
                from_email="new@example.com", api_key="new"
            ),
        ),
    )
    with pytest.raises(BadRequestError):
        await migrate_workspace(actor.id, workspace.id, apply=True)
    raw = await delivery.database().get("node", app.id)
    assert raw["context"]["settings"]["api_key"] == "legacy"


async def test_history_expiry_does_not_recreate_or_rebind_an_old_command(configured):
    message, result, _ = await queued(configured, "retained-command")
    await delivery.database().delete(
        "object", delivery.message_object(result.message_id)
    )
    replay = await delivery.enqueue_email(message, idempotency_key="retained-command")
    assert replay.message_id == result.message_id
    assert (
        await delivery.database().get(
            "object", delivery.message_object(result.message_id)
        )
        is None
    )
    message.subject = "Changed after history expiry"
    with pytest.raises(BadRequestError):
        await delivery.enqueue_email(message, idempotency_key="retained-command")


async def test_worker_refuses_replaced_intent_fingerprint(configured):
    from app.agentive.services import work_worker

    _, result, item_id = await queued(configured, "replaced-content")
    await delivery.database().find_one_and_update(
        "object",
        {"id": delivery.message_object(result.message_id)},
        {"$set": {"context.fingerprint": "changed"}},
    )
    before = delivery.deliver.await_count
    item = await work_worker.process_one_due_item(work_item_id=item_id)
    assert item.status == "failed"
    assert delivery.deliver.await_count == before


async def test_disable_then_reenable_cannot_revive_old_pending_mail(configured):
    from app.agentive.services import work_worker

    actor, workspace, active = configured
    _, result, item_id = await queued(configured, "disabled-generation")
    disabled = await config.activate(actor, workspace, active["revision"], disable=True)
    await config.test_draft(actor, workspace, disabled["revision"])
    await config.activate(actor, workspace, disabled["revision"])
    before = delivery.deliver.await_count
    await work_worker.process_one_due_item(work_item_id=item_id)
    assert delivery.deliver.await_count == before
    row = await delivery.database().get(
        "object", delivery.message_object(result.message_id)
    )
    assert row["context"]["status"] == "failed"
