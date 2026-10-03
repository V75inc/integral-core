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
