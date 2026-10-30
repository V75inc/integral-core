"""Real PostgreSQL invitation ordering and complete-grant rollback."""

import asyncio
import uuid

import pytest
from jvspatial.api.auth.models import User as AuthUser
from jvspatial.core.context import GraphContext, scoped_default_context_async
from jvspatial.db.factory import create_database

from app.models.edges import IS_MEMBER_OF
from app.models.nodes import Invitation, User
from app.services import invitations
from app.services.app_graph import catalog_user
from app.services.permissions import resolve_role
from tests.test_resource_location import graph


async def test_nested_issuance_refuses_before_state_or_token_delivery(
    postgres_raw_db, monkeypatch
):
    from app.services.app_operations.transaction_scope import (
        OperationTransactionUnavailable,
        postgres_graph_transaction,
    )

    deliveries = []

    async def delivered(*args, **kwargs):
        deliveries.append(True)

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, workspace, _, _, _ = await graph()
        email = f"nested-invitation-{uuid.uuid4().hex}@example.com"
        monkeypatch.setattr("app.services.email_delivery.enqueue_email", delivered)
        async with postgres_graph_transaction(postgres_raw_db):
            with pytest.raises(OperationTransactionUnavailable):
                await invitations.create_invitation(
                    workspace=workspace, inviter_user_id=owner.id, email=email
                )
        assert deliveries == []
        assert (
            await postgres_raw_db.find(
                "node",
                {
                    "entity": Invitation._entity_name(),
                    "context.email": email,
                },
            )
            == []
        )


@pytest.mark.parametrize("failure", ["catalog", "audit"])
@pytest.mark.parametrize("kind", ["workspace", "resource"])
async def test_issuance_fault_rolls_back_invitation_and_prevents_delivery(
    postgres_raw_db, monkeypatch, failure, kind
):
    from app.services.app_operations import event_outbox

    deliveries = []

    async def fail(*args, **kwargs):
        raise RuntimeError("issuance unit fault")

    async def delivered(*args, **kwargs):
        deliveries.append(True)

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, workspace, _, track, _ = await graph()
        email = f"issuance-rollback-{uuid.uuid4().hex}@example.com"
        if failure == "catalog":
            monkeypatch.setattr(invitations, "catalog_invitation", fail)
        else:
            monkeypatch.setattr(event_outbox, "insert_revision_event", fail)
        monkeypatch.setattr("app.services.email_delivery.enqueue_email", delivered)
        with pytest.raises(RuntimeError, match="issuance unit fault"):
            if kind == "workspace":
                await invitations.create_invitation(
                    workspace=workspace, inviter_user_id=owner.id, email=email
                )
            else:
                await invitations.create_resource_invitation(
                    resource_type="track",
                    resource_id=track.id,
                    workspace_id=workspace.id,
                    inviter_user_id=owner.id,
                    email=email,
                    role="viewer",
                )
        assert deliveries == []
        assert (
            await postgres_raw_db.find(
                "node",
                {
                    "entity": Invitation._entity_name(),
                    "context.email": email,
                },
            )
            == []
        )


async def test_expiry_commits_system_audit_intent_with_terminal_state(
    postgres_raw_db, monkeypatch
):
    from datetime import datetime, timedelta, timezone

    from app.services import change_event
    from app.services.app_operations.event_outbox import event_outbox_object_id

    async def fail_sink(**kwargs):
        raise RuntimeError("expiry sink offline")

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, _, _, _, invitation, _ = await resource_invitation()
        invitation.expires_at = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        await invitation.save()
        monkeypatch.setattr(change_event, "emit_change_event", fail_sink)
        assert await invitations.expire_invitations() == 1
        current = await Invitation.find_one({"id": invitation.id})
        assert current.status == "expired" and current.invitation_revision == 1
        row = await postgres_raw_db.find_one(
            "object", {"id": event_outbox_object_id(current.audit_outbox_id)}
        )
        assert row is not None
        assert row["context"]["event"]["actor_kind"] == "system"
        assert row["context"]["event"]["action"] == "workspace.invitation_expire"


async def test_owner_adoption_audit_failure_rolls_back_token_and_legacy_reconciliation(
    postgres_raw_db, monkeypatch
):
    from app.services.app_operations import event_outbox
    from app.services.invitation_adoption import (
        adopt_invitation,
        preview_invitation_adoption,
    )

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, workspace, track, recipient, invitation, token = (
            await resource_invitation()
        )
        old_hash = invitation.token_hash
        await postgres_raw_db.find_one_and_update(
            "node",
            {"id": invitation.id},
            {
                "$unset": {
                    "context.grant_baseline": "",
                    "context.issuer_membership_id": "",
                    "context.invitation_revision": "",
                }
            },
        )
        review = await preview_invitation_adoption(
            invitation.id, workspace.id, actor_id=owner.id
        )

        async def fail_intent(**kwargs):
            raise RuntimeError("adoption audit insertion fault")

        monkeypatch.setattr(event_outbox, "insert_revision_event", fail_intent)
        with pytest.raises(RuntimeError, match="adoption audit insertion"):
            await adopt_invitation(
                invitation.id,
                workspace.id,
                actor_id=owner.id,
                review_digest=review["review_digest"],
                role="viewer",
                can_create_apps=False,
                can_create_tracks=False,
            )
        row = await postgres_raw_db.find_one("node", {"id": invitation.id})
        assert row["context"]["token_hash"] == old_hash
        assert "invitation_revision" not in row["context"]
        assert "grant_baseline" not in row["context"]
        assert not row["context"].get("adopted_by_user_id")
        assert await resolve_role(recipient.id, "track", track.id) is None


pytestmark = [pytest.mark.postgres, pytest.mark.contract, pytest.mark.asyncio]


async def resource_invitation():
    """Issue a rooted grant to a real linked recipient identity."""
    owner, workspace, _, track, _ = await graph()
    identity = await AuthUser.create(
        email=f"invitation-transaction-{uuid.uuid4().hex}@example.com",
        password_hash="fixture",
    )
    recipient = await User.create(user_id=identity.id)
    await catalog_user(recipient)
    invitation, token, _ = await invitations.create_resource_invitation(
        resource_type="track",
        resource_id=track.id,
        workspace_id=workspace.id,
        inviter_user_id=owner.id,
        email=identity.email,
        role="viewer",
        send_email_notification=False,
    )
    return owner, workspace, track, recipient, invitation, token


async def test_fault_after_grant_rolls_back_membership_and_collaboration(
    postgres_raw_db, monkeypatch
):
    """Acceptance cannot commit a guest or collaborator without its terminal state."""
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, workspace, track, recipient, invitation, token = await resource_invitation()
        original = invitations._materialize_invitation_grants

        async def fail_after_grant(user, admitted):
            assert await original(user, admitted) is None
            raise RuntimeError("fault after actual grants")

        monkeypatch.setattr(
            invitations, "_materialize_invitation_grants", fail_after_grant
        )
        _, error = await invitations.consume_invitation_token(token, recipient.id)
        assert error == invitations.ERR_GRANT_FAILED
        current = await Invitation.find_one({"id": invitation.id})
        assert current.status == "pending" and current.invitation_revision == 0
        assert await resolve_role(recipient.id, "track", track.id) is None
        assert (
            await postgres_raw_db.find(
                "edge",
                {
                    "source": recipient.id,
                    "target": workspace.id,
                    "entity": IS_MEMBER_OF.__name__,
                },
            )
            == []
        )


async def test_accept_waits_for_other_connection_revoke_and_does_not_grant(
    postgres_raw_db, monkeypatch
):
    """A pending snapshot waits behind committed cancellation on another pool."""
    second = create_database(db_type="postgres", min_size=1, max_size=1)
    held, release, accepting = asyncio.Event(), asyncio.Event(), asyncio.Event()
    tasks = []
    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            owner, _, track, recipient, invitation, token = await resource_invitation()
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
                    accepting.set()
                    return await invitations.consume_invitation_token(
                        token, recipient.id
                    )

            tasks.append(asyncio.create_task(revoke()))
            await asyncio.wait_for(held.wait(), timeout=15)
            tasks.append(asyncio.create_task(accept()))
            await asyncio.wait_for(accepting.wait(), timeout=15)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(tasks[1]), timeout=0.15)
            release.set()
            await asyncio.wait_for(tasks[0], timeout=15)
            _, error = await asyncio.wait_for(tasks[1], timeout=15)
            assert error == invitations.ERR_REVOKED
            assert (
                await Invitation.find_one({"id": invitation.id})
            ).status == "revoked"
            assert await resolve_role(recipient.id, "track", track.id) is None
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await second.close()


async def test_audit_sink_failure_retains_accepted_grant_and_recoverable_intent(
    postgres_raw_db, monkeypatch
):
    """Post-commit delivery failure cannot lose intent or undo committed access."""
    from app.services.app_operations.event_outbox import (
        deliver_operation_event,
        event_outbox_object_id,
    )

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, _, track, recipient, invitation, token = await resource_invitation()

        async def unavailable_sink(**event):
            raise RuntimeError("audit sink unavailable")

        monkeypatch.setattr(
            "app.services.change_event.emit_change_event", unavailable_sink
        )
        accepted, error = await invitations.consume_invitation_token(
            token, recipient.id
        )
        assert error is None and accepted.status == "accepted"
        assert await resolve_role(recipient.id, "track", track.id) == "viewer"
        fact = await postgres_raw_db.find_one(
            "object", {"id": event_outbox_object_id(accepted.audit_outbox_id)}
        )
        assert fact["context"]["status"] == "pending"
        assert fact["context"]["event"]["before"]["status"] == "pending"
        assert fact["context"]["event"]["before"]["grant_outcome"] == ""
        assert fact["context"]["event"]["before"]["granted_role"] is None
        assert fact["context"]["event"]["after"]["id"] == invitation.id
        delivered = []

        async def restored_sink(**event):
            delivered.append(event)

        monkeypatch.setattr(
            "app.services.change_event.emit_change_event", restored_sink
        )
        assert await deliver_operation_event(
            outbox_id=accepted.audit_outbox_id, database=postgres_raw_db
        )
        assert len(delivered) == 1
        assert delivered[0]["after"]["invitation_revision"] == 1
        assert not await deliver_operation_event(
            outbox_id=accepted.audit_outbox_id, database=postgres_raw_db
        )
        assert len(delivered) == 1


async def test_audit_intent_write_failure_rolls_back_grants_and_terminal_state(
    postgres_raw_db, monkeypatch
):
    """Grant success cannot commit when its durable event fact cannot be written."""
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, workspace, track, recipient, invitation, token = await resource_invitation()

        async def cannot_persist(**kwargs):
            raise RuntimeError("outbox insert failure")

        monkeypatch.setattr(
            "app.services.app_operations.event_outbox.insert_revision_event",
            cannot_persist,
        )
        _, error = await invitations.consume_invitation_token(token, recipient.id)
        assert error == invitations.ERR_GRANT_FAILED
        current = await Invitation.find_one({"id": invitation.id})
        assert current.status == "pending"
        assert current.audit_outbox_id == invitation.audit_outbox_id
        assert await resolve_role(recipient.id, "track", track.id) is None
        assert (
            await postgres_raw_db.find(
                "edge",
                {
                    "source": recipient.id,
                    "target": workspace.id,
                    "entity": IS_MEMBER_OF.__name__,
                },
            )
            == []
        )


async def test_two_invitations_to_same_target_have_distinct_durable_event_identity(
    postgres_raw_db, monkeypatch
):
    """Different grants cannot collide when their target and revision match."""
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, workspace, track, recipient, first, first_token = (
            await resource_invitation()
        )
        identity = await AuthUser.create(
            email=f"second-invite-{uuid.uuid4().hex}@example.com",
            password_hash="fixture",
        )
        second_user = await User.create(user_id=identity.id)
        await catalog_user(second_user)
        second, second_token, _ = await invitations.create_resource_invitation(
            resource_type="track",
            resource_id=track.id,
            workspace_id=workspace.id,
            inviter_user_id=owner.id,
            email=identity.email,
            role="viewer",
            send_email_notification=False,
        )
        emitted = []

        async def capture(**event):
            emitted.append(event)

        monkeypatch.setattr("app.services.change_event.emit_change_event", capture)
        first_result, first_error = await invitations.consume_invitation_token(
            first_token, recipient.id
        )
        second_result, second_error = await invitations.consume_invitation_token(
            second_token, second_user.id
        )
        assert first_error is None and second_error is None
        assert first_result.audit_outbox_id != second_result.audit_outbox_id
        assert len(emitted) == 2
        assert {event["after"]["id"] for event in emitted} == {first.id, second.id}
        assert {event["resource_id"] for event in emitted} == {track.id}
        assert {event["after"]["invitation_revision"] for event in emitted} == {1}
