"""Canonical delivery response shares grant, history and audit storage admission."""

import asyncio

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async
from jvspatial.db.factory import create_database

from app.api.errors import ResourceConflictError
from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
from app.models.nodes import Invitation, Notification
from app.services import invitation_notifications, invitations
from app.services.app_graph import create_notification, ensure_integral_app_graph
from app.services.app_operations import event_outbox
from app.services.email_identity import current_account_email
from app.services.grant_history import read_grant_history
from app.services.invitation_grants import _current_grant
from tests.contract.test_invitation_lifecycle_postgres import resource_invitation

pytestmark = pytest.mark.contract


async def delivery():
    # This raw-store fixture deliberately skips ordinary app seeding. Build
    # the rooted shell explicitly instead of depending on test order.
    await ensure_integral_app_graph(include_library=False)
    owner, workspace, track, recipient, invitation, _ = await resource_invitation()
    email = await current_account_email(recipient)
    assert email == invitation.email
    recipient.email_verified = True
    recipient.verified_email = email
    await recipient.save()
    notice = await create_notification(
        user_id=recipient.id,
        type="invitation",
        content="Synthetic canonical delivery",
        metadata={
            "payload": {
                "invitation_id": invitation.id,
                "invitation_revision": invitation.invitation_revision,
            }
        },
    )
    return owner, workspace, track, recipient, invitation, notice


async def respond(recipient, notice, invitation):
    return await invitation_notifications.respond_to_invitation_notification(
        user_id=recipient.id,
        notification_id=notice.id,
        action="accept",
        invitation_revision=invitation.invitation_revision,
    )


@pytest.mark.parametrize("failure", ["delivery", "response_audit"])
async def test_response_storage_failure_rolls_back_entire_grant_unit(
    postgres_raw_db, monkeypatch, failure
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, workspace, track, recipient, invitation, notice = await delivery()
        facts_before = await postgres_raw_db.find(
            "object", {"entity": "OperationEventOutbox"}
        )
        if failure == "delivery":
            original = Notification.save

            async def fail(self, *args, **kwargs):
                if self.id == notice.id and self.metadata.get("response") == "accept":
                    raise RuntimeError("response storage fault")
                return await original(self, *args, **kwargs)

            monkeypatch.setattr(Notification, "save", fail)
        else:
            original = event_outbox.insert_revision_event

            async def fail(**kwargs):
                if kwargs["namespace"] == "invitation-notification-response-v1":
                    raise RuntimeError("response storage fault")
                return await original(**kwargs)

            monkeypatch.setattr(event_outbox, "insert_revision_event", fail)
        with pytest.raises(RuntimeError, match="response storage fault"):
            await respond(recipient, notice, invitation)
        current = await Invitation.find_one({"id": invitation.id})
        assert current.status == "pending" and current.invitation_revision == 0
        fresh = await Notification.find_one({"id": notice.id})
        assert fresh.read is False and "response" not in fresh.metadata
        for target, family in [(workspace, IS_MEMBER_OF), (track, COLLABORATES_ON)]:
            assert await _current_grant(recipient.id, target.id, family) is None
            assert await read_grant_history(recipient.id, target.id, family) is None
        assert (
            await postgres_raw_db.find("object", {"entity": "OperationEventOutbox"})
            == facts_before
        )


async def test_sink_failure_keeps_actual_response_and_pending_fact(
    postgres_raw_db, monkeypatch
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, _, track, recipient, invitation, notice = await delivery()

        async def fail(**event):
            raise RuntimeError("response audit sink unavailable")

        monkeypatch.setattr("app.services.change_event.emit_change_event", fail)
        result = await respond(recipient, notice, invitation)
        assert result["changed"] is True
        assert result["invitation"]["grant_outcome"] == "created"
        assert result["invitation"]["effective_role"] == "viewer"
        assert (await _current_grant(recipient.id, track.id, COLLABORATES_ON))[
            "context"
        ]["role"] == "viewer"
        facts = await postgres_raw_db.find(
            "object",
            {"entity": "OperationEventOutbox", "context.event.resource_id": notice.id},
        )
        assert len(facts) == 1 and facts[0]["context"]["status"] == "pending"
        assert facts[0]["context"]["event"]["after"]["metadata"]["response"] == "accept"
        retry = await respond(recipient, notice, invitation)
        assert retry["changed"] is False
        assert (
            await postgres_raw_db.find(
                "object",
                {
                    "entity": "OperationEventOutbox",
                    "context.event.resource_id": notice.id,
                },
            )
            == facts
        )


async def test_notification_response_waits_for_peer_revoke(
    postgres_raw_db, monkeypatch
):
    second = create_database(db_type="postgres", min_size=1, max_size=1)
    held, release, responding = asyncio.Event(), asyncio.Event(), asyncio.Event()
    tasks = []
    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            owner, workspace, track, recipient, invitation, notice = await delivery()
            original = invitations._revoke_admitted_invitation

            async def hold_revoke(current, *, actor_user_id):
                held.set()
                await asyncio.wait_for(release.wait(), timeout=15)
                await original(current, actor_user_id=actor_user_id)

            monkeypatch.setattr(invitations, "_revoke_admitted_invitation", hold_revoke)

            async def revoke():
                async with scoped_default_context_async(
                    GraphContext(database=postgres_raw_db)
                ):
                    await invitations.revoke_invitation(
                        invitation, actor_user_id=owner.id
                    )

            async def accept():
                async with scoped_default_context_async(GraphContext(database=second)):
                    responding.set()
                    return await respond(recipient, notice, invitation)

            tasks.append(asyncio.create_task(revoke()))
            await asyncio.wait_for(held.wait(), timeout=15)
            tasks.append(asyncio.create_task(accept()))
            await asyncio.wait_for(responding.wait(), timeout=15)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(tasks[1]), timeout=0.15)
            release.set()
            await asyncio.wait_for(tasks[0], timeout=15)
            with pytest.raises(ResourceConflictError):
                await asyncio.wait_for(tasks[1], timeout=15)
            assert (
                await Invitation.find_one({"id": invitation.id})
            ).status == "revoked"
            assert (await Notification.find_one({"id": notice.id})).read is False
            assert (
                await _current_grant(recipient.id, workspace.id, IS_MEMBER_OF) is None
            )
            assert await _current_grant(recipient.id, track.id, COLLABORATES_ON) is None
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await second.close()
