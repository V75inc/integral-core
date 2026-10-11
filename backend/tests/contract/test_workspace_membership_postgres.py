"""Independent PostgreSQL ordering and atomic membership/audit failures."""

import asyncio

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async
from jvspatial.db.factory import create_database

from app.api.errors import InsufficientPermissionsError
from app.services import invitations
from app.services import workspace_membership as membership
from app.services.app_operations import event_outbox
from tests.test_invitation_grant_outcomes import recipient_fixture
from tests.test_resource_location import graph
from tests.test_workspace_membership_authority import row, subject


@pytest.mark.parametrize("operation", ["add", "update", "remove"])
async def test_audit_intent_failure_rolls_back_complete_member_change(
    postgres_raw_db, monkeypatch, operation
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, workspace, _, _, _ = await graph()
        member = await subject(workspace, None if operation == "add" else "member")
        before = await row(member, workspace)

        async def fail(**kwargs):
            raise RuntimeError("member intent storage failed")

        monkeypatch.setattr(event_outbox, "insert_revision_event", fail)
        with pytest.raises(RuntimeError, match="member intent storage failed"):
            function = {
                "add": membership.add_member,
                "update": membership.update_member,
                "remove": membership.remove_member,
            }[operation]
            values = (
                {"role": "admin", "can_create_apps": True}
                if operation != "remove"
                else {}
            )
            await function(
                actor_id=owner.id,
                workspace_id=workspace.id,
                member_id=member.id,
                **values,
            )
        assert await row(member, workspace) == before


async def test_sink_failure_preserves_current_rights_and_complete_recoverable_fact(
    postgres_raw_db, monkeypatch
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, workspace, _, _, _ = await graph()
        member = await subject(workspace, "member")
        before = await row(member, workspace)

        async def fail(**kwargs):
            raise RuntimeError("member audit sink offline")

        monkeypatch.setattr("app.services.change_event.emit_change_event", fail)
        result = await membership.update_member(
            actor_id=owner.id,
            workspace_id=workspace.id,
            member_id=member.id,
            can_create_tracks=True,
        )
        facts = await postgres_raw_db.find(
            "object",
            {
                "entity": "OperationEventOutbox",
                "context.operation_key": "workspace.member_update",
            },
        )
        assert len(facts) == 1
        assert facts[0]["context"]["status"] == "pending"
        event = facts[0]["context"]["event"]
        assert event["before"]["can_create_tracks"] is False
        assert event["after"]["can_create_tracks"] is True
        assert (
            event["before"]["membership_id"]
            == event["after"]["membership_id"]
            == before["id"]
        )
        assert event["after"]["grant_revision"] == result["grant_revision"] == 1
        assert (await row(member, workspace))["context"]["can_create_tracks"] is True


@pytest.mark.parametrize("follower", ["invitation", "member_add"])
async def test_issuer_demotion_fences_pending_invitation_and_other_membership_writers(
    postgres_raw_db, monkeypatch, follower
):
    second = create_database(db_type="postgres", min_size=1, max_size=1)
    held, release, started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    tasks = []
    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            owner, workspace, _, recipient, email = await recipient_fixture()
            admin = await subject(workspace, "admin")
            _, token, _ = await invitations.create_invitation(
                workspace=workspace,
                inviter_user_id=admin.id,
                email=email,
                send_email_notification=False,
            )
            # Keep this barrier about the workspace row, not cold adapter DDL.
            # The separate cold single-connection test covers initialization.
            for collection in ("node", "edge", "object"):
                await second.find(collection, {"id": ""}, limit=1)
            original = membership._record_change

            async def hold(*args, **kwargs):
                held.set()
                await asyncio.wait_for(release.wait(), timeout=15)
                return await original(*args, **kwargs)

            monkeypatch.setattr(membership, "_record_change", hold)

            async def demote():
                async with scoped_default_context_async(
                    GraphContext(database=postgres_raw_db)
                ):
                    return await membership.update_member(
                        actor_id=owner.id,
                        workspace_id=workspace.id,
                        member_id=admin.id,
                        role="guest",
                    )

            async def follow():
                async with scoped_default_context_async(GraphContext(database=second)):
                    started.set()
                    if follower == "invitation":
                        return await invitations.consume_invitation_token(
                            token, recipient.id
                        )
                    return await membership.add_member(
                        actor_id=admin.id,
                        workspace_id=workspace.id,
                        member_id=recipient.id,
                    )

            tasks.append(asyncio.create_task(demote()))
            await asyncio.wait_for(held.wait(), timeout=15)
            tasks.append(asyncio.create_task(follow()))
            await asyncio.wait_for(started.wait(), timeout=15)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(tasks[1]), timeout=0.15)
            release.set()
            await asyncio.wait_for(tasks[0], timeout=15)
            if follower == "invitation":
                accepted, error = await asyncio.wait_for(tasks[1], timeout=15)
                assert (
                    accepted.status == "pending"
                    and error == invitations.ERR_GRANT_FAILED
                )
            else:
                with pytest.raises(InsufficientPermissionsError):
                    await asyncio.wait_for(tasks[1], timeout=15)
            assert await row(recipient, workspace) is None
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await second.close()


async def test_cold_single_connection_can_use_all_core_collections_in_transaction(
    postgres_raw_db,
):
    from app.services.app_operations.transaction_scope import postgres_graph_transaction

    second = create_database(db_type="postgres", min_size=1, max_size=1)
    try:

        async def read_transaction():
            async with postgres_graph_transaction(second) as transaction:
                for collection in ("node", "edge", "object"):
                    assert await transaction.find(collection, {"id": ""}, limit=1) == []

        await asyncio.wait_for(read_transaction(), timeout=15)
    finally:
        await second.close()
