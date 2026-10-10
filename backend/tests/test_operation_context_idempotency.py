"""Nested App operations retain the enclosing durable-operation identity."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.app_operations.context import OperationContext


@pytest.mark.asyncio
async def test_operation_context_propagates_idempotency_key_to_nested_operation():
    ctx = OperationContext(
        user_id="u-nested",
        workspace_id="ws-nested",
        scope="operation:app-nested:outer",
        app_id="app-nested",
        operation_key="outer",
        idempotency_key="logical-operation-key",
        correlation_id="correlation-nested",
    )
    with patch(
        "app.services.app_operations.dispatch.invoke_app_operation",
        new=AsyncMock(return_value={"ok": True}),
    ) as invoke:
        result = await ctx.invoke("inner", {"value": "x"})

    assert result == {"ok": True}
    assert invoke.await_args.kwargs == {
        "user_id": "u-nested",
        "workspace_id": "ws-nested",
        "app_id": "app-nested",
        "operation_key": "inner",
        "payload": {"value": "x"},
        "idempotency_key": "logical-operation-key",
        "correlation_id": "correlation-nested",
    }


@pytest.mark.asyncio
async def test_notify_once_identity_is_scoped_to_workspace_and_app():
    ctx = OperationContext(
        user_id="u-notify",
        workspace_id="ws-one",
        scope="operation:app-one:daily_check",
        app_id="app-one",
        operation_key="daily_check",
    )
    with patch(
        "app.services.app_graph.create_notification_once",
        new=AsyncMock(
            side_effect=[
                type("Note", (), {"id": "n1"})(),
                type("Note", (), {"id": "n2"})(),
            ]
        ),
    ) as create:
        assert await ctx.notify_once(dedupe_key="same", title="T", body="B") == "n1"
        ctx.app_id = "app-two"
        assert await ctx.notify_once(dedupe_key="same", title="T", body="B") == "n2"

    assert (
        create.await_args_list[0].kwargs["identity"]
        != create.await_args_list[1].kwargs["identity"]
    )


@pytest.mark.asyncio
async def test_durable_operation_cannot_write_attachment_outside_transaction():
    ctx = OperationContext(
        user_id="u-operation",
        workspace_id="ws-operation",
        scope="operation:app-one:write",
        app_id="app-one",
        deferred_change_events=[],
    )
    assert await ctx.put_attachment("entry-1", b"file", "test.txt") is None


@pytest.mark.asyncio
async def test_read_only_context_cannot_create_deduplicated_notice():
    ctx = OperationContext(
        user_id="u-notify",
        workspace_id="ws-one",
        scope="operation:app-one:read",
        app_id="app-one",
        read_only=True,
    )
    with patch(
        "app.services.app_graph.create_notification_once", new=AsyncMock()
    ) as create:
        assert await ctx.notify_once(dedupe_key="same", title="T", body="B") is None
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_notification_once_persists_one_rooted_notice(test_user):
    from app.models.edges import HAS_NOTIFICATION
    from app.models.nodes import Notification
    from app.services.app_graph import create_notification_once

    user_id = str(getattr(test_user, "user_id", None) or test_user.id)
    first = await create_notification_once(
        user_id=user_id,
        identity="test-identity-once",
        type="info",
        content="One logical notice",
    )
    second = await create_notification_once(
        user_id=user_id,
        identity="test-identity-once",
        type="info",
        content="One logical notice",
    )
    assert first.id == second.id
    assert isinstance(await Notification.get(first.id), Notification)
    context = await test_user.get_context()
    edges = await context.find_edges_between(
        test_user.id, first.id, edge_class=HAS_NOTIFICATION
    )
    assert len(edges) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stale", [False, True])
async def test_sdk_update_routes_revision_and_deferred_events_to_shared_command(stale):
    from types import SimpleNamespace

    from app.api.errors import ResourceConflictError
    from app.schemas.policy import Decision
    from app.services.app_invariant_guards import operation_write_active

    context = OperationContext(
        user_id="u-operation",
        workspace_id="ws-operation",
        scope="operation:app:edit",
        app_id="app-one",
        deferred_change_events=[],
    )
    entry = SimpleNamespace(id="entry-one", track_id="track-one", record_revision=2)
    track = SimpleNamespace(id="track-one", workspace_id="ws-operation")
    app = SimpleNamespace(
        workspace_id="ws-operation",
        lifecycle_state="active",
        nodes=AsyncMock(return_value=[track]),
    )

    async def shared_update(**kwargs):
        assert operation_write_active()
        assert kwargs["expected_record_revision"] == 1
        assert kwargs["workspace_id"] == context.workspace_id
        assert kwargs["change_event_sink"] == context.deferred_change_events.append
        if stale:
            raise ResourceConflictError(message="stale revision")
        return entry

    with (
        patch("app.models.nodes.Entry.get", new=AsyncMock(return_value=entry)),
        patch("app.models.nodes.Track.get", new=AsyncMock(return_value=track)),
        patch("app.models.nodes.App.get", new=AsyncMock(return_value=app)),
        patch(
            "app.services.permissions.resolve_role", new=AsyncMock(return_value="owner")
        ),
        patch(
            "app.services.policy_engine.evaluate",
            new=AsyncMock(return_value=Decision(allowed=True, reason="test")),
        ),
        patch(
            "app.services.entry_update.update_entry_in_track",
            new=AsyncMock(side_effect=shared_update),
        ) as update,
    ):
        saved = await context.update_entry_fields(
            "entry-one", {"phone": "600-0123"}, expected_record_revision=1
        )
    assert saved is not stale
    assert update.await_count == 1
    assert not operation_write_active()
