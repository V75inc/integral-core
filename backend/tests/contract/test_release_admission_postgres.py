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


async def test_committed_chat_stream_rechecks_admission_after_workspace_revocation(
    postgres_raw_db, monkeypatch
):
    """A previously authorized SSE page cannot release its next frame after revoke.

    Admission linearizes before transport handoff. Revocation may commit while
    the client is processing a frame, but every subsequent frame must be
    admitted again. No database transaction is held while the client consumes
    the yielded bytes.
    """
    from unittest.mock import AsyncMock

    from app.schemas.agentive.chat_events import ChatEventReplayPage
    from app.services import chat_turn_streaming

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, _ = await proposal()
        replay = AsyncMock(
            return_value=ChatEventReplayPage(
                events=[
                    {"sequence": 1, "type": "text-delta", "delta": "Admitted"},
                    {"sequence": 2, "type": "text-delta", "delta": "Must withhold"},
                ],
                committed_through=2,
                work_status="succeeded",
            )
        )
        monkeypatch.setattr(chat_turn_streaming, "replay_work_item_chat_events", replay)

        async def assert_current_scope():
            async with release_admission(auth.id, workspace.id):
                pass

        response = chat_turn_streaming.stream_committed_chat_turn(
            principal_id=auth.id,
            workspace_id=workspace.id,
            thread_id="thread-release-probe",
            work_item_id="work-release-probe",
            assert_scope=assert_current_scope,
            poll_seconds=0,
        )
        first_frame = await anext(response)
        assert b"Admitted" in first_frame

        await remove_membership(auth, workspace)

        with pytest.raises(InsufficientPermissionsError):
            await anext(response)
        assert replay.await_count == 1
        await response.aclose()


@pytest.mark.parametrize("resource_kind", ["App", "Track", "Entry"])
async def test_queued_events_withhold_outsider_without_closing_independent_sessions(
    postgres_raw_db,
    resource_kind,
):
    """A normal denial must not break delivery of a later authorized frame."""
    import json

    from app.models.edges import COLLABORATES_ON, CONTAINS
    from app.models.nodes import App, Entry
    from app.services.change_event_logger import ChangeEventEnvelope
    from app.services.event_subscription_registry import (
        queue_change_event,
        register_subscription,
        unregister_subscription,
    )

    class Socket:
        def __init__(self):
            self.messages = []
            self.closed = []
            self.received = asyncio.Event()

        async def send_text(self, text):
            with pytest.raises(OperationTransactionUnavailable):
                current_graph_transaction_database()
            self.messages.append(json.loads(text)["payload"])
            self.received.set()

        async def close(self, **kwargs):
            self.closed.append(kwargs)

    def event(track, actor, event_id):
        resource = shared_resource if track.id == shared_track.id else track
        kind = resource_kind if track.id == shared_track.id else "Track"
        scope = f"app:{resource.id}" if kind == "App" else f"track:{track.id}"
        return ChangeEventEnvelope(
            id=event_id,
            ts="2026-11-15T00:00:00Z",
            actor_kind="human",
            actor_id=actor.id,
            action=f"{kind.lower()}.update",
            resource_type=kind,
            resource_id=resource.id,
            scope=scope,
            before=None,
            after={"id": resource.id, "title": "Private current title"},
        )

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, workspace, track, _ = await proposal()
        shared_track = track
        shared_resource = track
        if resource_kind == "App":
            parents = await track.nodes(
                edge=[CONTAINS], direction="in", node=[App._entity_name()], limit=2
            )
            assert len(parents) == 1
            shared_resource = parents[0]
        elif resource_kind == "Entry":
            shared_resource = await Entry.create(
                title="Private current title", track_id=track.id
            )
            await track.connect(shared_resource, edge=CONTAINS)
        collaborator, _, collaborator_track, _ = await proposal()
        outsider, _, own_track, _ = await proposal()
        profile = (await User.find({"user_id": collaborator.id}))[0]
        await profile.connect(workspace, edge=IS_MEMBER_OF, role="member")
        await profile.connect(
            shared_resource if resource_kind == "App" else track,
            edge=COLLABORATES_ON,
            role="viewer",
        )
        sockets = [
            (owner, Socket()),
            (owner, Socket()),
            (collaborator, Socket()),
            (outsider, Socket()),
        ]
        try:
            for principal, socket in sockets:
                await register_subscription(principal.id, socket)
            queue_change_event(event(track, owner, "shared-frame"))
            queue_change_event(event(own_track, outsider, "own-frame"))
            await asyncio.wait_for(
                asyncio.gather(*(socket.received.wait() for _, socket in sockets)), 10
            )
            assert [
                [item["id"] for item in socket.messages] for _, socket in sockets
            ] == [
                ["shared-frame"],
                ["shared-frame"],
                ["shared-frame"],
                ["own-frame"],
            ]
            assert all(not socket.closed for _, socket in sockets)
            # Reconnect is a new transport, not a new permission grant.
            await unregister_subscription(outsider.id, sockets[-1][1])
            reconnect = Socket()
            sockets.append((outsider, reconnect))
            await register_subscription(outsider.id, reconnect)
            collaborator_socket = sockets[2][1]
            collaborator_socket.received.clear()
            queue_change_event(event(track, owner, "shared-after-reconnect"))
            queue_change_event(event(own_track, outsider, "own-after-reconnect"))
            await asyncio.wait_for(
                asyncio.gather(
                    reconnect.received.wait(), collaborator_socket.received.wait()
                ),
                10,
            )
            assert [item["id"] for item in reconnect.messages] == [
                "own-after-reconnect"
            ]
            assert not reconnect.closed
            collaborator_socket.received.clear()
            await remove_membership(collaborator, workspace)
            queue_change_event(event(track, owner, "shared-after-revocation"))
            queue_change_event(
                event(collaborator_track, collaborator, "own-after-revocation")
            )
            await asyncio.wait_for(collaborator_socket.received.wait(), 10)
            assert [item["id"] for item in collaborator_socket.messages] == [
                "shared-frame",
                "shared-after-reconnect",
                "own-after-revocation",
            ]
            assert not collaborator_socket.closed
        finally:
            for principal, socket in sockets:
                await unregister_subscription(principal.id, socket)


async def test_independent_principals_keep_chat_replay_and_sessions_separate(
    postgres_raw_db, monkeypatch
):
    """Two scoped submissions complete without sharing their durable output."""
    import base64

    from app.agentive.services import execution_runs, work_worker
    from app.services import chat_providers
    from app.services.chat_turn_events import replay_work_item_chat_events
    from tests.native_harness.wp_03.test_chat_turn_worker_postgres import (
        _fake_start_run,
        _Provider,
        _Registry,
        _submitted_claimed_turn,
    )

    monkeypatch.setenv(
        "INTEGRAL_CREDENTIAL_ENC_KEY", base64.b64encode(b"c" * 32).decode()
    )

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        first, first_principal = await _submitted_claimed_turn(claim=False)
        second, second_principal = await _submitted_claimed_turn(claim=False)
        assert first_principal != second_principal
        assert first.thread_id != second.thread_id
        provider = _Provider(
            [
                {"type": "text-delta", "delta": "Isolated answer"},
                {"type": "message-finish", "contextMode": "conversation"},
            ]
        )
        monkeypatch.setattr(chat_providers, "get_registry", lambda: _Registry(provider))
        monkeypatch.setattr(execution_runs, "start_run", _fake_start_run)
        for item in (first, second):
            result = await work_worker.process_one_due_item(
                work_item_id=item.work_item_id,
                worker_id=f"independent-{item.work_item_id}",
                lease_seconds=120,
            )
            assert result is not None
            assert result.status == "succeeded", result.failure
        for own, other, principal in (
            (first, second, first_principal),
            (second, first, second_principal),
        ):
            page = await replay_work_item_chat_events(
                principal_id=principal,
                workspace_id=own.workspace_id,
                thread_id=own.thread_id,
                work_item_id=own.work_item_id,
            )
            assert any(event.get("delta") == "Isolated answer" for event in page.events)
            with pytest.raises(ValueError, match="chat replay scope mismatch"):
                await replay_work_item_chat_events(
                    principal_id=principal,
                    workspace_id=other.workspace_id,
                    thread_id=other.thread_id,
                    work_item_id=other.work_item_id,
                )
