"""Notification delivery requires its actual current recipient, never snapshot claims."""

from unittest.mock import AsyncMock

import pytest

from app.models.edges import HAS_NOTIFICATION
from app.services.app_graph import create_notification
from app.services.change_event_logger import ChangeEventEnvelope
from app.services.event_access import _can_release_event_current, prepare_event_payload

pytestmark = pytest.mark.smoke


def notification_event(notification, user, **overrides):
    return ChangeEventEnvelope(
        **{
            "id": "notification-event",
            "ts": "2026-10-21T12:00:00Z",
            "actor_kind": "human",
            "actor_id": "someone-else",
            "action": "notification.create",
            "resource_type": "Notification",
            "resource_id": notification.id,
            "scope": f"user:{user.id}",
            "before": None,
            "after": {"id": notification.id, "type": "mention", "user_id": user.id},
            **overrides,
        }
    )


@pytest.mark.asyncio
async def test_owned_notification_releases_minimal_event(test_user):
    notification = await create_notification(
        user_id=test_user.id, type="mention", content="Private content"
    )
    event = notification_event(notification, test_user)
    from app.schemas.policy import Decision

    allow = AsyncMock(return_value=Decision(allowed=True, reason="test"))
    assert await _can_release_event_current(
        test_user.user_id, event, action="event_feed.subscribe", evaluate=allow
    )
    payload = await prepare_event_payload(
        test_user.user_id, event, action="event_feed.subscribe", evaluate=allow
    )
    assert payload is not None
    assert payload["after"]["id"] == notification.id
    assert "Private content" not in str(payload)


@pytest.mark.asyncio
async def test_notification_rejects_forged_scope_missing_edge_and_deleted_record(
    test_user,
):
    from app.schemas.policy import Decision

    allow = AsyncMock(return_value=Decision(allowed=True, reason="test"))
    notification = await create_notification(
        user_id=test_user.id, type="mention", content="Private content"
    )
    event = notification_event(notification, test_user, scope="user:someone-else")
    assert not await _can_release_event_current(
        test_user.user_id, event, action="event_feed.subscribe", evaluate=allow
    )
    event = notification_event(notification, test_user)
    await test_user.disconnect(notification, edge_type=HAS_NOTIFICATION)
    assert not await _can_release_event_current(
        test_user.user_id, event, action="event_feed.subscribe", evaluate=allow
    )
    await notification.delete()
    assert not await _can_release_event_current(
        test_user.user_id, event, action="event_feed.subscribe", evaluate=allow
    )


@pytest.mark.asyncio
async def test_notification_rejects_another_recipient_even_when_snapshot_claims_caller(
    test_user, test_user2
):
    from app.schemas.policy import Decision

    allow = AsyncMock(return_value=Decision(allowed=True, reason="test"))
    notification = await create_notification(
        user_id=test_user2.id, type="mention", content="Other person"
    )
    event = notification_event(notification, test_user)
    assert not await _can_release_event_current(
        test_user.user_id, event, action="event_feed.subscribe", evaluate=allow
    )


@pytest.mark.asyncio
async def test_live_notification_and_alias_replay_update_recipient_only(
    authenticated_client, test_user, test_user2
):
    import json

    from app.services.event_subscription_registry import (
        register_subscription,
        unregister_subscription,
    )
    from app.services.notification_router import dispatch

    owner_socket = AsyncMock()
    outsider_socket = AsyncMock()
    await register_subscription(test_user.user_id, owner_socket)
    await register_subscription(test_user2.user_id, outsider_socket)
    try:
        result = await dispatch(
            user_id=test_user.id,
            kind="mention",
            payload={"snippet": "A mention"},
            actor_id=test_user2.id,
            actor_kind="human",
            channels=["in_app"],
        )
        notified_id = result["notification_id"]
        delivered = [
            json.loads(call.args[0])["payload"]
            for call in owner_socket.send_text.await_args_list
        ]
        assert any(
            p["resource_id"] == notified_id and p["after"]["type"] == "mention"
            for p in delivered
        )
        assert not any(
            notified_id in str(call)
            for call in outsider_socket.send_text.await_args_list
        )
        for identity in [test_user.id, test_user.user_id]:
            response = await authenticated_client.get(
                "/api/events", params={"scope": f"user:{identity}"}
            )
            assert response.status_code == 200, response.text
            assert any(
                p["resource_id"] == notified_id for p in response.json()["events"]
            ), response.text
    finally:
        await unregister_subscription(test_user.user_id, owner_socket)
        await unregister_subscription(test_user2.user_id, outsider_socket)


@pytest.mark.asyncio
async def test_mention_reference_uses_current_record_and_live_entry_access(test_user):
    from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
    from app.services.policy_engine import evaluate
    from tests.test_comment_permission_matrix import _org_fixture

    workspace, _, track, entry = await _org_fixture("mention-reference")
    await test_user.connect(workspace, edge=IS_MEMBER_OF, role="member")
    await test_user.connect(track, edge=COLLABORATES_ON, role="viewer")
    notification = await create_notification(
        user_id=test_user.id,
        type="mention",
        content="A comment",
        metadata={"payload": {"entry_id": entry.id}},
    )
    event = notification_event(
        notification,
        test_user,
        after={"entry_id": "n.Entry.forged", "secret": "Do not release"},
    )
    payload = await prepare_event_payload(
        test_user.user_id, event, action="event_feed.subscribe", evaluate=evaluate
    )
    assert payload["after"]["entry_id"] == entry.id
    assert "forged" not in str(payload) and "Do not release" not in str(payload)
    await test_user.disconnect(workspace, edge_type=IS_MEMBER_OF)
    payload = await prepare_event_payload(
        test_user.user_id, event, action="event_feed.subscribe", evaluate=evaluate
    )
    assert payload is not None and "entry_id" not in payload["after"]


@pytest.mark.asyncio
async def test_org_workspace_notification_history_does_not_include_account_profile(
    authenticated_client, test_user, test_user2
):
    from app.models.edges import IS_MEMBER_OF
    from app.services.change_event_logger import get_change_event_logger
    from app.services.notification_router import dispatch
    from tests.test_comment_permission_matrix import _org_fixture

    workspace, _, _, _ = await _org_fixture("notification-replay")
    await test_user.connect(workspace, edge=IS_MEMBER_OF, role="member")
    dispatched = await dispatch(
        user_id=test_user.id,
        kind="mention",
        payload={},
        actor_id=test_user2.id,
        actor_kind="human",
        channels=["in_app"],
    )
    profile = notification_event(
        type("Record", (), {"id": test_user.id})(),
        test_user,
        resource_type="User",
        action="user.update",
    )
    profile.id = ""
    await get_change_event_logger().persist(profile)
    response = await authenticated_client.get(
        "/api/events",
        params={"scope": f"user:{test_user.user_id}"},
        headers={"X-Integral-Scope": f"ws:{workspace.id}"},
    )
    assert response.status_code == 200, response.text
    assert any(
        p["resource_id"] == dispatched["notification_id"]
        for p in response.json()["events"]
    ), response.text
    assert not any(p["id"] == profile.id for p in response.json()["events"])
