"""Notification release, recipient roles and nontransactional system delivery."""

from unittest.mock import AsyncMock

import pytest

from app.api.errors import InsufficientPermissionsError
from app.models.edges import (
    COLLABORATES_ON,
    CONTAINS,
    EXCLUDED_FROM,
    HAS_NOTIFICATION,
    IS_MEMBER_OF,
)
from app.models.nodes import Track, User
from app.services import email_delivery as delivery
from app.services.app_graph import catalog_track, catalog_user, create_notification
from app.services.email_service import EmailMessage
from tests.test_resource_location import graph

pytestmark = [pytest.mark.asyncio, pytest.mark.smoke]


async def notice(role="member"):
    owner, workspace, app, track, entry = await graph()
    recipient = await User.create(
        display_name="Email recipient", preferences={"email": "recipient@example.com"}
    )
    await catalog_user(recipient)
    await recipient.connect(workspace, edge=IS_MEMBER_OF, role=role)
    grant = await recipient.connect(track, edge=COLLABORATES_ON, role="viewer")
    notification = await create_notification(
        user_id=recipient.id,
        type="mention",
        content="Private title",
        metadata={
            "email_workspace_id": workspace.id,
            "payload": {"entry_id": entry.id, "track_id": track.id},
        },
    )
    message = EmailMessage(
        to="recipient@example.com",
        subject="Private title",
        html="<p>Private title</p>",
        text="Private title",
        workspace_id=workspace.id,
        actor_user_id=recipient.id,
        source_kind="notification",
        source_id=notification.id,
    )
    return owner, recipient, workspace, app, track, entry, grant, notification, message


@pytest.mark.parametrize(
    "change", ["grant", "exclude", "delete", "move", "foreign", "recipient"]
)
async def test_resource_changes_withhold_previously_admitted_notice(change):
    _, recipient, workspace, app, track, entry, grant, notification, message = (
        await notice()
    )
    await delivery._admit_source(message)
    assert message.notification_scope
    if change == "grant":
        await grant.delete()
    elif change == "exclude":
        await recipient.connect(entry, edge=EXCLUDED_FROM)
    elif change == "delete":
        await entry.delete(cascade=False)
    elif change == "move":
        target = await Track.create(title="Other", workspace_id=workspace.id)
        await catalog_track(target)
        await app.connect(target, edge=CONTAINS)
        await recipient.connect(target, edge=COLLABORATES_ON, role="viewer")
        await track.disconnect(entry, edge_type=CONTAINS)
        await target.connect(entry, edge=CONTAINS)
        entry.track_id = target.id
        await entry.save()
    elif change == "foreign":
        _, _, _, _, elsewhere = await graph()
        notification.metadata["payload"] = {"entry_id": elsewhere.id}
        await notification.save()
    else:
        await recipient.disconnect(notification, edge_type=HAS_NOTIFICATION)
    with pytest.raises(InsufficientPermissionsError):
        await delivery._admit_source(message)


async def test_guest_receives_direct_entry_notice_but_cannot_originate_app_email():
    _, recipient, workspace, _, _, entry, grant, _, message = await notice("guest")
    await grant.delete()
    await recipient.connect(entry, edge=COLLABORATES_ON, role="viewer")
    await delivery._admit(recipient.id, workspace.id, notification=True)
    await delivery._admit_source(message)
    with pytest.raises(InsufficientPermissionsError):
        await delivery._admit(recipient.id, workspace.id)


async def test_system_notice_requires_recipient_and_workspace_but_no_resource():
    _, recipient, workspace, _, _, _, _, notification, message = await notice()
    notification.metadata["payload"] = {}
    await notification.save()
    await delivery._admit_source(message)
    assert message.notification_scope == {}


async def test_json_invitation_uses_configured_platform_delivery(monkeypatch):
    from app.services import email_service
    from app.services.invitations import create_invitation

    owner, workspace, *_ = await graph()
    send = AsyncMock(return_value=True)
    queue = AsyncMock()
    monkeypatch.setattr(email_service, "send_email", send)
    monkeypatch.setattr(delivery, "enqueue_email", queue)
    monkeypatch.setattr(
        "app.services.email_configuration.storage_available", lambda: False
    )
    invitation, _, _ = await create_invitation(
        workspace=workspace,
        inviter_user_id=owner.id,
        email="invited@example.com",
        send_email_notification=True,
    )
    assert invitation.status == "pending"
    send.assert_awaited_once()
    queue.assert_not_awaited()
    assert send.call_args.args[0].to == "invited@example.com"


async def test_system_transport_failure_never_retries_another_provider(monkeypatch):
    from app.services import email_service

    *_, message = await notice()
    send = AsyncMock(return_value=False)
    monkeypatch.setattr(email_service, "send_email", send)
    monkeypatch.setattr(
        "app.services.email_configuration.storage_available", lambda: False
    )
    assert (await delivery.send_system_email(message, idempotency_key="notice"))[
        "status"
    ] == "failed"
    send.assert_awaited_once()


async def test_immediate_notice_rechecks_revocation_before_transport(monkeypatch):
    from app.services import email_service

    _, _, _, _, _, _, grant, _, message = await notice()
    await grant.delete()
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(email_service, "send_email", send)
    monkeypatch.setattr(
        "app.services.email_configuration.storage_available", lambda: False
    )
    with pytest.raises(InsufficientPermissionsError):
        await delivery.send_system_email(message, idempotency_key="revoked")
    send.assert_not_awaited()


async def test_unrelated_parent_reference_requires_its_own_read_permission():
    _, _, workspace, app, _, entry, _, notification, message = await notice()
    other = await Track.create(title="Private other track", workspace_id=workspace.id)
    await catalog_track(other)
    await app.connect(other, edge=CONTAINS)
    notification.metadata["payload"] = {
        "entry_id": entry.id,
        "resource_type": "track",
        "resource_id": other.id,
    }
    await notification.save()
    with pytest.raises(InsufficientPermissionsError):
        await delivery._admit_source(message)
