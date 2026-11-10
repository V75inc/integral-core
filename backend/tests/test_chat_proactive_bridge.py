"""Tests for proactive message → ChatThread bridge."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


def _retry_delivery_in_new_process(dsn, args, results):
    """Restore the delivery from a new process and database connection."""
    import asyncio

    async def run():
        from jvspatial.core.context import GraphContext, set_default_context
        from jvspatial.db.factory import create_database

        from app.services import chat_proactive_bridge as bridge

        database = create_database(db_type="postgres", dsn=dsn)
        set_default_context(GraphContext(database=database))
        bridge.notify_thread_message = AsyncMock()
        try:
            results.put(
                {"thread": await bridge.persist_proactive_message_on_thread(**args)}
            )
        finally:
            await database.close()

    try:
        asyncio.run(run())
    except Exception as exc:
        results.put({"error": f"{type(exc).__name__}: {exc}"})


@pytest.mark.asyncio
async def test_persist_proactive_message_on_thread_returns_none_when_missing() -> None:
    """A missing exact destination produces no delivery."""
    from app.services.chat_proactive_bridge import persist_proactive_message_on_thread

    with patch(
        "app.services.chat_proactive_bridge.find_thread_by_provider_session",
        new=AsyncMock(return_value=None),
    ):
        result = await persist_proactive_message_on_thread(
            user_id="u1",
            workspace_id="ws1",
            session_id="sess-1",
            content="Hello",
            delivery_id="Hello",
        )
    assert result is None


@pytest.mark.asyncio
async def test_delivery_refuses_nontransactional_store(monkeypatch) -> None:
    """A local adapter cannot claim durable idempotency without transactions."""
    from app.api.errors import OperationTransactionUnavailableError
    from app.services import chat_proactive_bridge as bridge

    monkeypatch.setattr(
        bridge, "find_thread_by_provider_session", AsyncMock(return_value=object())
    )
    from app.services.app_operations import transaction_scope

    monkeypatch.setattr(transaction_scope, "graph_transaction_available", lambda: False)
    with pytest.raises(OperationTransactionUnavailableError):
        await bridge.persist_proactive_message_on_thread(
            user_id="u1",
            workspace_id="ws1",
            session_id="sess-1",
            content="Heads up",
            delivery_id="notice-1",
        )


@pytest.mark.asyncio
async def test_session_lookup_refuses_cross_workspace_and_ambiguous_bindings(
    monkeypatch,
):
    """A provider session cannot redirect delivery to another workspace."""
    from types import SimpleNamespace

    from app.services import chat_proactive_bridge as bridge

    rows = [
        SimpleNamespace(
            id="other",
            user_id="u1",
            workspace_id="ws2",
            provider_session_id="same-session",
            archived=False,
        ),
        SimpleNamespace(
            id="wanted",
            user_id="u1",
            workspace_id="ws1",
            provider_session_id="same-session",
            archived=False,
        ),
    ]

    async def find(model, query, **kwargs):
        assert model is bridge.ChatThread
        return [
            row
            for row in rows
            if all(
                getattr(row, key.removeprefix("context.")) == value
                for key, value in query.items()
            )
        ][: kwargs["limit"]]

    monkeypatch.setattr(
        bridge, "get_default_context", lambda: SimpleNamespace(find=find)
    )
    monkeypatch.setattr(
        bridge, "can_access_workspace", AsyncMock(return_value="member")
    )
    match = await bridge.find_thread_by_provider_session(
        user_id="u1", workspace_id="ws1", provider_session_id="same-session"
    )
    assert match.id == "wanted"
    assert (
        await bridge.find_thread_by_provider_session(
            user_id="u1", workspace_id="ws3", provider_session_id="same-session"
        )
        is None
    )
    rows.append(SimpleNamespace(**vars(rows[1])))
    assert (
        await bridge.find_thread_by_provider_session(
            user_id="u1", workspace_id="ws1", provider_session_id="same-session"
        )
        is None
    )


@pytest.mark.asyncio
async def test_revoked_access_and_missing_binding_never_append(monkeypatch):
    """Revocation, a missing session, and an empty scope fail closed."""
    from types import SimpleNamespace

    from app.services import chat_proactive_bridge as bridge

    lookup = AsyncMock()
    append = AsyncMock()
    monkeypatch.setattr(
        bridge, "get_default_context", lambda: SimpleNamespace(find=lookup)
    )
    monkeypatch.setattr(bridge, "can_access_workspace", AsyncMock(return_value="none"))
    monkeypatch.setattr(bridge.chat_store, "append_message", append)
    for workspace, session in (("ws1", "sess"), ("ws1", ""), ("", "sess")):
        assert (
            await bridge.persist_proactive_message_on_thread(
                user_id="u1",
                workspace_id=workspace,
                session_id=session,
                content="private",
                delivery_id="private",
            )
            is None
        )
    lookup.assert_not_awaited()
    append.assert_not_awaited()


@pytest.mark.asyncio
async def test_lookup_rechecks_hydrated_identity(monkeypatch):
    """A stale or malformed candidate cannot cross the scope boundary."""
    from types import SimpleNamespace

    from app.services import chat_proactive_bridge as bridge

    thread = SimpleNamespace(
        id="t",
        user_id="u1",
        workspace_id="other",
        provider_session_id="sess",
        archived=False,
    )
    monkeypatch.setattr(
        bridge,
        "get_default_context",
        lambda: SimpleNamespace(find=AsyncMock(return_value=[thread])),
    )
    monkeypatch.setattr(
        bridge, "can_access_workspace", AsyncMock(return_value="member")
    )
    assert (
        await bridge.find_thread_by_provider_session(
            user_id="u1", workspace_id="ws1", provider_session_id="sess"
        )
        is None
    )


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_proactive_delivery_stays_in_the_bound_workspace(monkeypatch):
    """Actual chat records receive messages only for the exact authorized scope."""
    from jvspatial.api.auth.models import User as AuthUser

    from app.models.nodes import User
    from app.services import chat_proactive_bridge as bridge
    from app.services import chat_threads
    from app.services.app_graph import catalog_user, ensure_integral_app_graph
    from tests.fixtures.workspaces import make_org_workspace

    await ensure_integral_app_graph(include_library=False)
    await AuthUser.create(
        id="o.User.proactive-scope",
        email="proactive-scope@example.com",
        password_hash="fixture",
    )
    user = await User.create(user_id="o.User.proactive-scope", display_name="Tester")
    await catalog_user(user)
    first = await make_org_workspace("First", owner=user)
    second = await make_org_workspace("Second", owner=user)
    thread_a = await chat_threads.create_thread(
        user_id=user.user_id, provider_id="integral_native", workspace_id=first.id
    )
    thread_b = await chat_threads.create_thread(
        user_id=user.user_id, provider_id="integral_native", workspace_id=second.id
    )
    await chat_threads.update_provider_session(thread_a, "session-a")
    await chat_threads.update_provider_session(thread_b, "session-b")
    notify = AsyncMock()
    monkeypatch.setattr(bridge, "notify_thread_message", notify)
    assert await bridge.can_access_workspace(user.user_id, first.id) != "none"
    stored = await bridge.ChatThread.get(thread_a.id)
    assert stored.provider_session_id == "session-a"
    assert (
        await bridge.find_thread_by_provider_session(
            user_id=user.user_id, workspace_id=first.id, provider_session_id="session-a"
        )
        is not None
    )

    assert (
        await bridge.persist_proactive_message_on_thread(
            user_id=user.user_id,
            workspace_id=first.id,
            session_id="session-b",
            content="secret",
            delivery_id="secret",
        )
        is None
    )
    assert await chat_threads.list_messages(thread_a) == []
    assert await chat_threads.list_messages(thread_b) == []
    assert (
        await bridge.persist_proactive_message_on_thread(
            user_id=user.user_id,
            workspace_id=first.id,
            session_id="session-a",
            content="safe",
            delivery_id="safe",
        )
        == thread_a.id
    )
    assert len(await chat_threads.list_messages(thread_a)) == 1
    assert await chat_threads.list_messages(thread_b) == []
    assert (
        await bridge.persist_proactive_message_on_thread(
            user_id=user.user_id,
            workspace_id=first.id,
            session_id=thread_b.id,
            content="wrong",
            delivery_id="wrong",
        )
        is None
    )
    assert (
        await bridge.persist_proactive_message_on_thread(
            user_id=user.user_id,
            workspace_id=first.id,
            session_id=thread_a.id,
            content="native",
            delivery_id="native",
        )
        == thread_a.id
    )
    assert len(await chat_threads.list_messages(thread_a)) == 2
    assert await chat_threads.list_messages(thread_b) == []
    assert notify.await_count == 2


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_locked_delivery_refuses_a_stale_archived_candidate(monkeypatch):
    """A destination archived after lookup is rechecked under its row lock."""
    from jvspatial.api.auth.models import User as AuthUser

    from app.models.nodes import ChatThread, User
    from app.services import chat_proactive_bridge as bridge
    from app.services import chat_threads
    from app.services.app_graph import catalog_user, ensure_integral_app_graph
    from tests.fixtures.workspaces import make_org_workspace

    await ensure_integral_app_graph(include_library=False)
    user_id = "o.User.proactive-archive"
    await AuthUser.create(
        id=user_id, email="proactive-archive@example.com", password_hash="fixture"
    )
    user = await User.create(user_id=user_id, display_name="Tester")
    await catalog_user(user)
    workspace = await make_org_workspace("Proactive archive", owner=user)
    thread = await chat_threads.create_thread(
        user_id=user_id, provider_id="integral_native", workspace_id=workspace.id
    )
    stale_candidate = await ChatThread.get(thread.id)
    current = await ChatThread.get(thread.id)
    await chat_threads.archive_thread(current)
    monkeypatch.setattr(
        bridge,
        "find_thread_by_provider_session",
        AsyncMock(return_value=stale_candidate),
    )
    notify = AsyncMock()
    monkeypatch.setattr(bridge, "notify_thread_message", notify)
    assert (
        await bridge.persist_proactive_message_on_thread(
            user_id=user_id,
            workspace_id=workspace.id,
            session_id=thread.id,
            content="private",
            delivery_id="private",
        )
        is None
    )
    assert await chat_threads.list_messages(thread) == []
    notify.assert_not_awaited()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_delivery_retry_and_concurrency_reconcile_the_same_message(monkeypatch):
    """A post-commit notification failure and simultaneous retry cannot duplicate."""
    import asyncio

    from jvspatial.api.auth.models import User as AuthUser

    from app.api.errors import ResourceConflictError
    from app.models.nodes import User
    from app.services import chat_proactive_bridge as bridge
    from app.services import chat_threads
    from app.services.app_graph import catalog_user, ensure_integral_app_graph
    from tests.fixtures.workspaces import make_org_workspace

    await ensure_integral_app_graph(include_library=False)
    user_id = "o.User.proactive-retry"
    await AuthUser.create(
        id=user_id, email="proactive-retry@example.com", password_hash="fixture"
    )
    user = await User.create(user_id=user_id, display_name="Tester")
    await catalog_user(user)
    workspace = await make_org_workspace("Retry", owner=user)
    thread = await chat_threads.create_thread(
        user_id=user_id, provider_id="integral_native", workspace_id=workspace.id
    )
    other = await chat_threads.create_thread(
        user_id=user_id, provider_id="integral_native", workspace_id=workspace.id
    )
    notification = AsyncMock(side_effect=[RuntimeError("offline"), None, None, None])
    monkeypatch.setattr(bridge, "notify_thread_message", notification)
    args = {
        "user_id": user_id,
        "workspace_id": workspace.id,
        "session_id": thread.id,
        "content": "A verified update",
        "delivery_id": "event-1",
        "metadata": {"channel": "in_app"},
    }
    with pytest.raises(RuntimeError, match="offline"):
        await bridge.persist_proactive_message_on_thread(**args)
    assert len(await chat_threads.list_messages(thread)) == 1

    # A fresh process opens its own pool after the first process committed.
    import multiprocessing

    from jvspatial.core.context import get_default_context

    dsn = str(get_default_context().database.dsn)
    ctx = multiprocessing.get_context("spawn")
    results = ctx.Queue()
    process = ctx.Process(
        target=_retry_delivery_in_new_process, args=(dsn, args, results)
    )
    process.start()
    await asyncio.to_thread(process.join, 30)
    if process.is_alive():
        process.terminate()
        await asyncio.to_thread(process.join, 5)
        pytest.fail("fresh-process retry did not finish")
    assert process.exitcode == 0
    assert results.get(timeout=5) == {"thread": thread.id}
    results.close()
    assert len(await chat_threads.list_messages(thread)) == 1
    assert await bridge.persist_proactive_message_on_thread(**args) == thread.id
    assert len(await chat_threads.list_messages(thread)) == 1
    with pytest.raises(ResourceConflictError):
        await bridge.persist_proactive_message_on_thread(
            **{**args, "content": "changed"}
        )
    with pytest.raises(ResourceConflictError):
        await bridge.persist_proactive_message_on_thread(
            **{**args, "session_id": other.id}
        )
    assert await chat_threads.list_messages(other) == []

    concurrent = {**args, "delivery_id": "event-2"}
    assert await asyncio.gather(
        bridge.persist_proactive_message_on_thread(**concurrent),
        bridge.persist_proactive_message_on_thread(**concurrent),
    ) == [thread.id, thread.id]
    assert len(await chat_threads.list_messages(thread)) == 2
    assert notification.await_count == 4

    notification.side_effect = None
    race = {**args, "delivery_id": "event-3"}
    outcomes = await asyncio.gather(
        bridge.persist_proactive_message_on_thread(**race),
        bridge.persist_proactive_message_on_thread(**{**race, "session_id": other.id}),
        return_exceptions=True,
    )
    assert sum(isinstance(value, ResourceConflictError) for value in outcomes) == 1
    assert sum(isinstance(value, str) for value in outcomes) == 1
    assert (
        len(await chat_threads.list_messages(thread))
        + len(await chat_threads.list_messages(other))
        == 3
    )

    # If the message write fails, the receipt rolls back with it and a
    # subsequent retry may safely perform the original write.
    original_append = bridge.chat_store.append_message
    monkeypatch.setattr(
        bridge.chat_store,
        "append_message",
        AsyncMock(side_effect=RuntimeError("write failed")),
    )
    failure_args = {**args, "delivery_id": "event-rollback"}
    with pytest.raises(RuntimeError, match="write failed"):
        await bridge.persist_proactive_message_on_thread(**failure_args)
    monkeypatch.setattr(bridge.chat_store, "append_message", original_append)
    assert await bridge.persist_proactive_message_on_thread(**failure_args) == thread.id

    from app.agentive.harness.turn_input import thread_scope_key
    from app.models.harness_records import HarnessProactiveDeliveryRecord

    await chat_threads.delete_thread_messages(thread)
    scope = thread_scope_key(
        principal_id=user_id, workspace_id=workspace.id, thread_id=thread.id
    )
    assert await HarnessProactiveDeliveryRecord.find({"scope_key": scope}) == []
