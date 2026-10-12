"""Actual collaboration producer rollback and durable audit intent contracts."""

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
from app.services import sharing
from app.services.app_operations import event_outbox
from app.services.grant_history import read_grant_history
from app.services.invitation_grants import _current_grant
from tests.test_resource_location import graph
from tests.test_workspace_membership_authority import subject


@pytest.mark.parametrize("operation", ["add", "update", "remove"])
async def test_collaboration_audit_failure_rolls_back_grant_history_and_notifications(
    postgres_raw_db, monkeypatch, operation
):
    """Roll back grant, clocks, guest membership and notification on intent failure."""
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, workspace, app, _, _ = await graph()
        member = await subject(workspace, None if operation == "add" else "member")
        if operation != "add":
            await sharing.add_collaborator(owner.id, "app", app.id, member.id, "viewer")
        grant_before = await _current_grant(member.id, app.id, COLLABORATES_ON)
        clock_before = await read_grant_history(member.id, app.id, COLLABORATES_ON)
        member_before = await _current_grant(member.id, workspace.id, IS_MEMBER_OF)
        member_clock_before = await read_grant_history(
            member.id, workspace.id, IS_MEMBER_OF
        )
        notices_before = await postgres_raw_db.find("node", {"entity": "Notification"})
        facts_before = await postgres_raw_db.find(
            "object", {"entity": "OperationEventOutbox"}
        )

        async def fail(**kwargs):
            if kwargs["event"]["resource_type"] == "App":
                raise RuntimeError("collaboration intent failed")
            return await original(**kwargs)

        original = event_outbox.insert_revision_event
        monkeypatch.setattr(event_outbox, "insert_revision_event", fail)
        with pytest.raises(RuntimeError, match="collaboration intent failed"):
            if operation == "add":
                await sharing.add_collaborator(
                    owner.id, "app", app.id, member.id, "viewer"
                )
            elif operation == "update":
                await sharing.update_collaborator_role(
                    owner.id, "app", app.id, member.id, "editor"
                )
            else:
                await sharing.remove_collaborator(owner.id, "app", app.id, member.id)
        assert await _current_grant(member.id, app.id, COLLABORATES_ON) == grant_before
        assert (
            await read_grant_history(member.id, app.id, COLLABORATES_ON) == clock_before
        )
        assert (
            await _current_grant(member.id, workspace.id, IS_MEMBER_OF) == member_before
        )
        assert (
            await read_grant_history(member.id, workspace.id, IS_MEMBER_OF)
            == member_clock_before
        )
        assert (
            await postgres_raw_db.find("node", {"entity": "Notification"})
            == notices_before
        )
        assert (
            await postgres_raw_db.find("object", {"entity": "OperationEventOutbox"})
            == facts_before
        )


async def test_collaboration_sink_failure_keeps_actual_revision_and_pending_fact(
    postgres_raw_db, monkeypatch
):
    """Keep committed rights and a recoverable fact when the audit sink fails."""
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, workspace, app, _, _ = await graph()
        member = await subject(workspace, "member")
        await sharing.add_collaborator(owner.id, "app", app.id, member.id, "viewer")
        before = await _current_grant(member.id, app.id, COLLABORATES_ON)

        async def fail(**kwargs):
            raise RuntimeError("collaboration sink offline")

        monkeypatch.setattr("app.services.change_event.emit_change_event", fail)
        result = await sharing.update_collaborator_role(
            owner.id, "app", app.id, member.id, "editor"
        )
        facts = await postgres_raw_db.find(
            "object",
            {
                "entity": "OperationEventOutbox",
                "context.operation_key": "app.collaborator_role_update",
            },
        )
        assert len(facts) == 1 and facts[0]["context"]["status"] == "pending"
        event = facts[0]["context"]["event"]
        assert event["before"]["role"] == "viewer"
        assert event["after"]["role"] == result["role"] == "editor"
        assert event["before"]["grant_id"] == event["after"]["grant_id"] == before["id"]
        assert event["after"]["grant_revision"] == 1
        clock = await read_grant_history(member.id, app.id, COLLABORATES_ON)
        assert event["after"]["history_revision"] == clock["context"]["revision"] == 2
        assert (await _current_grant(member.id, app.id, COLLABORATES_ON))["context"][
            "role"
        ] == "editor"


async def test_collaborator_revocation_orders_peer_grant_admission(
    postgres_raw_db, monkeypatch
):
    """Order peer grant admission after committed administrator revocation."""
    import asyncio

    from jvspatial.db.factory import create_database

    from app.api.errors import InsufficientPermissionsError

    second = create_database(db_type="postgres", min_size=1, max_size=1)
    held, release, started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    tasks = []
    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            owner, workspace, app, _, _ = await graph()
            administrator = await subject(workspace, "member")
            recipient = await subject(workspace, "member")
            await sharing.add_collaborator(
                owner.id, "app", app.id, administrator.id, "admin"
            )
            for collection in ("node", "edge", "object"):
                await second.find(collection, {"id": ""}, limit=1)
            original = event_outbox.insert_revision_event

            async def hold(**kwargs):
                if kwargs["event"]["action"] == "app.collaborator_remove":
                    held.set()
                    await asyncio.wait_for(release.wait(), timeout=15)
                return await original(**kwargs)

            monkeypatch.setattr(event_outbox, "insert_revision_event", hold)

            async def revoke():
                async with scoped_default_context_async(
                    GraphContext(database=postgres_raw_db)
                ):
                    return await sharing.remove_collaborator(
                        owner.id, "app", app.id, administrator.id
                    )

            async def grant():
                async with scoped_default_context_async(GraphContext(database=second)):
                    started.set()
                    return await sharing.add_collaborator(
                        administrator.id, "app", app.id, recipient.id, "viewer"
                    )

            tasks.append(asyncio.create_task(revoke()))
            await asyncio.wait_for(held.wait(), timeout=15)
            tasks.append(asyncio.create_task(grant()))
            await asyncio.wait_for(started.wait(), timeout=15)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(tasks[1]), timeout=0.15)
            release.set()
            await asyncio.wait_for(tasks[0], timeout=15)
            with pytest.raises(InsufficientPermissionsError):
                await asyncio.wait_for(tasks[1], timeout=15)
            assert await _current_grant(recipient.id, app.id, COLLABORATES_ON) is None
            assert (
                await read_grant_history(recipient.id, app.id, COLLABORATES_ON) is None
            )
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await second.close()
