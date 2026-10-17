"""Tool audits share a write's commit and never broadcast inside it."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.services.app_operations import transaction_scope as seam
from app.services.hooks.registry import ToolContext


@pytest.fixture
def audit_scope(monkeypatch):
    committed = []
    sink = AsyncMock()

    class Transaction:
        def __init__(self):
            self.pending = []

        async def insert_if_absent(self, collection, document):
            assert collection == "object"
            self.pending.append(document)

    @asynccontextmanager
    async def graph(database):
        transaction = Transaction()
        context = type("Context", (), {"database": transaction})()
        yield context
        committed.extend(transaction.pending)

    monkeypatch.setattr(seam, "_jv_graph_transaction", graph)
    monkeypatch.setattr("app.services.change_event.emit_change_event", sink)
    return committed, sink


@pytest.mark.asyncio
async def test_tool_audits_commit_with_write_without_broadcast(audit_scope):
    committed, sink = audit_scope
    ctx = ToolContext(user_id="owner", workspace_id="workspace", scope="entry:form")
    async with seam.postgres_graph_transaction(object()):
        await ctx.emit_audit("tool.invoke", {"key": "hook"})
        await ctx.emit_audit("tool.invoke", {"key": "hook"})
        assert committed == []
        sink.assert_not_awaited()
    assert len(committed) == 2
    assert committed[0]["id"] != committed[1]["id"]
    event = committed[0]["context"]["event"]
    assert event["actor_id"] == "owner"
    assert event["scope"] == "entry:form"
    assert event["resource_id"] == ""
    assert event["after"] == {"details": {"key": "hook"}, "tool_invocation": True}
    sink.assert_not_awaited()


@pytest.mark.asyncio
async def test_rolled_back_write_does_not_commit_or_broadcast_audit(audit_scope):
    committed, sink = audit_scope
    ctx = ToolContext(user_id="owner", workspace_id="workspace", scope="entry:form")
    with pytest.raises(ValueError, match="rollback"):
        async with seam.postgres_graph_transaction(object()):
            await ctx.emit_audit("tool.invoke", {})
            raise ValueError("rollback")
    assert committed == []
    sink.assert_not_awaited()


@pytest.mark.asyncio
async def test_nontransactional_audit_retains_immediate_delivery(audit_scope):
    _, sink = audit_scope
    await ToolContext(
        user_id="owner", workspace_id="workspace", scope="ws:workspace"
    ).emit_audit("tool.invoke", {"key": "read"})
    sink.assert_awaited_once()


@pytest.mark.asyncio
async def test_inherited_transaction_cannot_emit_audit(audit_scope):
    committed, sink = audit_scope
    ctx = ToolContext(user_id="owner", workspace_id="workspace", scope="entry:form")
    async with seam.postgres_graph_transaction(object()):
        with pytest.raises(seam.OperationTransactionUnavailable):
            await asyncio.create_task(ctx.emit_audit("tool.invoke", {}))
    assert committed == []
    sink.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_transaction_cannot_escape_to_immediate_delivery(audit_scope):
    committed, sink = audit_scope
    ctx = ToolContext(user_id="owner", workspace_id="workspace", scope="entry:form")
    with pytest.raises(seam.OperationTransactionUnavailable):
        async with seam.postgres_graph_transaction(object()):
            with pytest.raises(ValueError):
                async with seam.postgres_graph_transaction():
                    raise ValueError("failed nested write")
            await ctx.emit_audit("tool.invoke", {})
    assert committed == []
    sink.assert_not_awaited()


@pytest.mark.postgres
@pytest.mark.asyncio
@pytest.mark.parametrize("rollback", [False, True])
async def test_postgres_audit_commit_recovery_and_rollback(
    postgres_raw_db, monkeypatch, rollback
):
    from app.services.app_operations.event_outbox import (
        deliver_pending_operation_events,
    )

    sink = AsyncMock()
    monkeypatch.setattr("app.services.change_event.emit_change_event", sink)
    ctx = ToolContext(user_id="owner", workspace_id="workspace", scope="entry:form")
    try:
        async with seam.postgres_graph_transaction(postgres_raw_db):
            await ctx.emit_audit("tool.invoke", {"key": "hook"})
            sink.assert_not_awaited()
            if rollback:
                raise ValueError("rollback")
    except ValueError:
        assert rollback
    await deliver_pending_operation_events(database=postgres_raw_db)
    if rollback:
        sink.assert_not_awaited()
    else:
        sink.assert_awaited_once()
        event = sink.await_args.kwargs
        assert event["actor_id"] == "owner"
        assert event["after"]["details"] == {"key": "hook"}
        assert event["details"]["event_fact_id"]
        await deliver_pending_operation_events(database=postgres_raw_db)
        sink.assert_awaited_once()
