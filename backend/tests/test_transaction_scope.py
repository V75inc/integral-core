"""Graph transaction scope binds the prime store when none is passed."""

from contextlib import asynccontextmanager

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("rollback", [False, True])
async def test_permission_nodes_do_not_cross_transaction_contexts(
    monkeypatch, rollback
):
    from app.middleware.permissions_cache import permissions_cache_get
    from app.services.app_operations.transaction_scope import postgres_graph_transaction

    @asynccontextmanager
    async def fake(database):
        class Context:
            database = "transaction"

        yield Context()

    monkeypatch.setattr(
        "app.services.app_operations.transaction_scope._jv_graph_transaction", fake
    )
    permissions_cache_get()[("user_node", "principal")] = "parent-context-user"
    try:
        async with postgres_graph_transaction(object()):
            assert permissions_cache_get() == {}
            permissions_cache_get()[("user_node", "principal")] = "transaction-user"
            if rollback:
                raise ValueError("rollback")
    except ValueError:
        assert rollback
    assert permissions_cache_get() == {}


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_new_app_ownership_is_visible_before_transaction_commit(
    postgres_raw_db, monkeypatch
):
    from jvspatial.core.context import GraphContext, scoped_default_context_async

    from app.models.edges import OWNS
    from app.models.nodes import App, User
    from app.services import permissions
    from app.services.app_graph import catalog_app, ensure_integral_app_graph
    from app.services.app_operations.transaction_scope import postgres_graph_transaction
    from app.services.personal_workspace import ensure_personal_workspace

    monkeypatch.setattr(permissions, "_permission_memo_enabled", lambda: True)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        user = await User.create(display_name="Transaction QA")
        await ensure_integral_app_graph(include_library=False)
        workspace = await ensure_personal_workspace(user)
        # The native chat loads this principal before entering its effect scope.
        resolved = await permissions.get_user_node(user.id)
        assert resolved is not None and resolved.id == user.id
        async with postgres_graph_transaction(postgres_raw_db):
            app = await App.create(
                name="Transaction QA",
                visibility="private",
                workspace_id=workspace.id,
            )
            await catalog_app(app)
            owner = await permissions.get_user_node(user.id)
            await owner.connect(app, edge=OWNS, role="owner")
            assert await permissions.can_admin_app(user.id, app.id)
        # Subsequent requests must not retain a node on the closed connection.
        assert await permissions.can_admin_app(user.id, app.id)


@pytest.mark.asyncio
async def test_omitted_database_uses_the_prime_store(monkeypatch):
    prime = object()
    seen = {}

    @asynccontextmanager
    async def fake(database):
        seen["database"] = database

        class _Ctx:
            database = "txn-handle"

        yield _Ctx()

    monkeypatch.setattr(
        "app.services.app_operations.transaction_scope._jv_graph_transaction",
        fake,
    )
    monkeypatch.setattr(
        "app.services.app_operations.transaction_scope.get_prime_database",
        lambda: prime,
    )
    from app.services.app_operations.transaction_scope import (
        postgres_graph_transaction,
    )

    async with postgres_graph_transaction() as handle:
        assert handle == "txn-handle"
    assert seen["database"] is prime


@pytest.mark.asyncio
async def test_nested_transaction_joins_handle_and_caught_failure_requires_rollback(
    monkeypatch,
):
    from app.services.app_operations import transaction_scope as seam

    calls = []
    database = object()

    @asynccontextmanager
    async def fake(bound):
        calls.append("begin")

        class Context:
            database = "held-handle"

        try:
            yield Context()
        except BaseException:
            calls.append("rollback")
            raise
        else:
            calls.append("commit")

    monkeypatch.setattr(seam, "_jv_graph_transaction", fake)
    async with seam.postgres_graph_transaction(database) as outer:
        async with seam.postgres_graph_transaction() as inner:
            assert inner is outer
        assert seam.graph_transaction_available(outer)
        async with seam.postgres_graph_transaction(outer) as explicit:
            assert explicit is outer
    assert calls == ["begin", "commit"]
    calls.clear()
    with pytest.raises(seam.OperationTransactionUnavailable, match="requires rollback"):
        async with seam.postgres_graph_transaction(database):
            try:
                async with seam.postgres_graph_transaction():
                    raise ValueError("partial write")
            except ValueError:
                pass
    assert calls == ["begin", "rollback"]


@pytest.mark.asyncio
async def test_nested_transaction_cannot_escape_store_or_inherit_into_child_task(
    monkeypatch,
):
    import asyncio

    from app.services.app_operations import transaction_scope as seam

    @asynccontextmanager
    async def fake(bound):
        class Context:
            database = "held-handle"

        yield Context()

    monkeypatch.setattr(seam, "_jv_graph_transaction", fake)

    async def child():
        with pytest.raises(seam.OperationTransactionUnavailable, match="another task"):
            seam.graph_transaction_available()
        with pytest.raises(seam.OperationTransactionUnavailable, match="another task"):
            async with seam.postgres_graph_transaction():
                pass

    async with seam.postgres_graph_transaction(object()):
        with pytest.raises(
            seam.OperationTransactionUnavailable, match="different database"
        ):
            async with seam.postgres_graph_transaction(object()):
                pass
        await asyncio.create_task(child())
