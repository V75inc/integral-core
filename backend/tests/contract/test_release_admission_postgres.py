"""Release admission orders with revocation but never holds transport I/O."""

import asyncio

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.api.errors import InsufficientPermissionsError
from app.models.edges import IS_MEMBER_OF
from app.models.nodes import User
from app.services.app_operations.transaction_scope import (
    OperationTransactionUnavailable,
    current_graph_transaction_database,
)
from app.services.membership_write_scope import membership_write_scope
from app.services.release_admission import release_admission
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def remove_membership(auth, workspace):
    profile = (await User.find({"user_id": auth.id}))[0]
    async with membership_write_scope(workspace.id, principal_ids=(auth.id,)):
        context = await profile.get_context()
        for edge in await context.find_edges_between(
            profile.id, workspace.id, edge_class=IS_MEMBER_OF
        ):
            await edge.delete()


async def test_revocation_waits_for_admission_but_not_for_the_transport(
    postgres_raw_db,
):
    admitted, finish_admission, sending, finish_send = [
        asyncio.Event() for _ in range(4)
    ]
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, _ = await proposal()

        async def release():
            async with release_admission(auth.id, workspace.id):
                admitted.set()
                await finish_admission.wait()
            with pytest.raises(OperationTransactionUnavailable):
                current_graph_transaction_database()
            sending.set()
            await finish_send.wait()

        releasing = asyncio.create_task(release())
        await asyncio.wait_for(admitted.wait(), 10)
        revoking = asyncio.create_task(remove_membership(auth, workspace))
        await asyncio.sleep(0.05)
        assert not revoking.done()
        finish_admission.set()
        await asyncio.wait_for(sending.wait(), 10)
        try:
            await asyncio.wait_for(revoking, 10)
            assert not releasing.done()  # Network remains stalled independently.
            with pytest.raises(InsufficientPermissionsError):
                async with release_admission(auth.id, workspace.id):
                    pytest.fail("A later frame was admitted after revocation")
        finally:
            finish_send.set()
        await releasing


async def test_socket_admission_rechecks_each_connection(postgres_raw_db):
    from app.services.change_event_logger import ChangeEventEnvelope
    from app.services.event_subscription_registry import (
        _subscriptions,
        broadcast_change_event,
        register_subscription,
    )

    entered, unblock = asyncio.Event(), asyncio.Event()
    delivered = []

    class Socket:
        async def send_text(self, value):
            with pytest.raises(OperationTransactionUnavailable):
                current_graph_transaction_database()
            delivered.append(value)
            entered.set()
            await unblock.wait()

    class LaterSocket:
        async def send_text(self, value):
            pytest.fail("Later connection received a frame after revocation")

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, track, _ = await proposal()
        event = ChangeEventEnvelope(
            id="release-probe",
            ts="2026-10-14T00:00:00Z",
            actor_kind="human",
            actor_id=auth.id,
            action="track.update",
            resource_type="Track",
            resource_id=track.id,
            scope=f"track:{track.id}",
            before=None,
            after={"id": track.id},
        )
        _subscriptions.clear()
        await register_subscription(auth.id, Socket())
        await register_subscription(auth.id, LaterSocket())
        sending = asyncio.create_task(broadcast_change_event(event))
        try:
            await asyncio.wait_for(entered.wait(), 10)
            await asyncio.wait_for(remove_membership(auth, workspace), 10)
        finally:
            unblock.set()
        await sending
        assert len(delivered) == 1
        _subscriptions.clear()
