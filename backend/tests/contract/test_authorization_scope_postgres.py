"""Real-store authorization lock and rollback proofs; no process-lock substitute."""

import asyncio

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.models.edges import COLLABORATES_ON, HAS_AUTHORIZATION_STATE
from app.models.nodes import App, Workspace, WorkspaceAuthorizationState
from app.services.app_operations.transaction_scope import postgres_graph_transaction
from app.services.authorization_scope import authorization_scope, authorization_state_id

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def test_explicit_held_handles_join_and_caught_failure_rolls_back(
    postgres_raw_db,
):
    from jvspatial.core.context import get_default_context

    from app.services.app_graph import catalog_workspace, ensure_integral_app_graph
    from app.services.app_operations.transaction_scope import (
        OperationTransactionUnavailable,
        graph_transaction_available,
    )

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        await ensure_integral_app_graph(include_library=False)
        workspace = await Workspace.create(name="Held handle QA")
        await catalog_workspace(workspace)
        async with postgres_graph_transaction(postgres_raw_db) as outer:
            held = get_default_context().database
            assert held is outer and graph_transaction_available(held)
            async with postgres_graph_transaction(held) as joined:
                assert joined is outer
                current = await Workspace.find_one({"id": workspace.id})
                current.name = "Committed nested write"
                await current.save()
            async with postgres_graph_transaction(held.inner) as raw_joined:
                assert raw_joined is outer
        assert (
            await Workspace.find_one({"id": workspace.id})
        ).name == "Committed nested write"
        with pytest.raises(OperationTransactionUnavailable, match="requires rollback"):
            async with postgres_graph_transaction(postgres_raw_db) as outer:
                try:
                    async with postgres_graph_transaction(outer):
                        current = await Workspace.find_one({"id": workspace.id})
                        current.name = "Must roll back"
                        await current.save()
                        raise ValueError("partial effect")
                except ValueError:
                    pass
        assert (
            await Workspace.find_one({"id": workspace.id})
        ).name == "Committed nested write"


async def test_revision_is_rooted_and_survives_stale_workspace_settings_save(
    postgres_raw_db,
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        from app.services.app_graph import catalog_workspace, ensure_integral_app_graph

        await ensure_integral_app_graph(include_library=False)
        workspace = await Workspace.create(name="Authorization QA")
        await catalog_workspace(workspace)
        stale = await Workspace.find_one({"id": workspace.id})
        async with authorization_scope(workspace.id, authority_mutation=True) as first:
            assert first.revision == 1
        stale.name = "Ordinary settings save"
        await stale.save()
        async with authorization_scope(workspace.id) as second:
            assert second.revision == 1
        state = await WorkspaceAuthorizationState.find_one(
            {"id": authorization_state_id(workspace.id)}
        )
        assert state.workspace_id == workspace.id
        context = await state.get_context()
        assert (
            len(
                await context.find_edges_between(
                    workspace.id, state.id, edge_class=HAS_AUTHORIZATION_STATE
                )
            )
            == 1
        )


async def test_nested_authority_scope_joins_effect_and_revision_rollback(
    postgres_raw_db,
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        from app.services.app_graph import catalog_workspace, ensure_integral_app_graph

        await ensure_integral_app_graph(include_library=False)
        workspace = await Workspace.create(name="Rollback QA")
        await catalog_workspace(workspace)
        state_id = authorization_state_id(workspace.id)
        with pytest.raises(ValueError, match="fail effect"):
            async with postgres_graph_transaction(postgres_raw_db):
                async with authorization_scope(workspace.id, authority_mutation=True):
                    current_workspace = await Workspace.find_one({"id": workspace.id})
                    current_workspace.name = "Uncommitted"
                    await current_workspace.save()
                    raise ValueError("fail effect")
        assert await WorkspaceAuthorizationState.find_one({"id": state_id}) is None
        assert (await Workspace.find_one({"id": workspace.id})).name == "Rollback QA"
        async with authorization_scope(
            workspace.id, authority_mutation=True
        ) as receipt:
            assert receipt.revision == 1
        with pytest.raises(ValueError):
            async with authorization_scope(
                workspace.id, authority_mutation=True
            ) as receipt:
                assert receipt.revision == 2
                raise ValueError("fail again")
        async with authorization_scope(workspace.id) as receipt:
            assert receipt.revision == 1


async def test_independent_connections_order_revocation_before_waiting_effect(
    postgres_raw_db,
):
    from app.models.edges import IS_MEMBER_OF
    from app.models.nodes import User
    from app.services.app_graph import (
        catalog_app,
        catalog_user,
        catalog_workspace,
        ensure_integral_app_graph,
    )
    from app.services.permissions import resolve_role

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        await ensure_integral_app_graph(include_library=False)
        user = await User.create(display_name="Reader")
        await catalog_user(user)
        workspace = await Workspace.create(name="Race QA")
        await catalog_workspace(workspace)
        await user.connect(workspace, edge=IS_MEMBER_OF, role="member")
        app = await App.create(
            name="Race QA", workspace_id=workspace.id, visibility="private"
        )
        await catalog_app(app)
        edge = await user.connect(app, edge=COLLABORATES_ON, role="editor")
        # Warm graph objects before the second connection waits behind revoke.
        assert await resolve_role(user.id, "app", app.id) == "editor"
        revoke_locked = asyncio.Event()
        effect_started = asyncio.Event()
        release_revoke = asyncio.Event()
        effect_done = asyncio.Event()
        result = []

        async def revoke():
            async with scoped_default_context_async(
                GraphContext(database=postgres_raw_db)
            ):
                async with authorization_scope(workspace.id, authority_mutation=True):
                    revoke_locked.set()
                    await release_revoke.wait()
                    current = await type(edge).find_one({"$and": [{"id": edge.id}]})
                    await current.delete()

        async def effect():
            await revoke_locked.wait()
            async with scoped_default_context_async(
                GraphContext(database=postgres_raw_db)
            ):
                effect_started.set()
                async with authorization_scope(workspace.id) as revision:
                    result.append(
                        (revision.revision, await resolve_role(user.id, "app", app.id))
                    )
                effect_done.set()

        tasks = [asyncio.create_task(revoke()), asyncio.create_task(effect())]
        try:
            await asyncio.wait_for(effect_started.wait(), timeout=10)
            # The held row lock must keep the second connection outside its
            # authoritative decision boundary until the revocation commits.
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(effect_done.wait(), timeout=0.1)
            release_revoke.set()
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)
            assert result == [(1, None)]
        finally:
            release_revoke.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def test_other_process_warm_permission_cache_cannot_cross_committed_revocation(
    postgres_raw_db,
):
    import json
    import sys
    import textwrap

    from app.models.edges import IS_MEMBER_OF
    from app.models.nodes import User
    from app.services.app_graph import (
        catalog_app,
        catalog_user,
        catalog_workspace,
        ensure_integral_app_graph,
    )

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        await ensure_integral_app_graph(include_library=False)
        user = await User.create(display_name="Worker QA")
        await catalog_user(user)
        workspace = await Workspace.create(name="Worker QA")
        await catalog_workspace(workspace)
        await user.connect(workspace, edge=IS_MEMBER_OF, role="member")
        app = await App.create(
            name="Worker QA", workspace_id=workspace.id, visibility="private"
        )
        await catalog_app(app)
        edge = await user.connect(app, edge=COLLABORATES_ON, role="editor")
        script = textwrap.dedent(
            """
            import asyncio, json, sys
            from jvspatial.db.factory import create_database
            from jvspatial.db import get_database_manager
            from jvspatial.core.context import GraphContext, scoped_default_context_async
            from app.services.authorization_scope import authorization_scope
            from app.services.permissions import resolve_role
            from app.services import permissions_process_cache as cache
            async def run():
                database = create_database(db_type="postgres", min_size=1, max_size=1)
                get_database_manager().set_prime_database(database)
                cache._ENABLED = True
                try:
                    async with scoped_default_context_async(GraphContext(database=database)):
                        user_id, app_id, workspace_id = sys.argv[1:]
                        assert await resolve_role(user_id, "app", app_id) == "editor"
                        assert cache.get_resolve_role_cached(user_id, "app", app_id) == "editor"
                        print("READY", flush=True)
                        assert await asyncio.to_thread(sys.stdin.readline) == "GO\\n"
                        async with authorization_scope(workspace_id) as receipt:
                            role = await resolve_role(user_id, "app", app_id)
                            print(json.dumps([receipt.revision, role]), flush=True)
                finally:
                    await database.close()
            asyncio.run(run())
        """
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            user.id,
            app.id,
            workspace.id,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            ready = await asyncio.wait_for(process.stdout.readline(), timeout=20)
            assert ready == b"READY\n"
            async with authorization_scope(workspace.id, authority_mutation=True):
                current_edge = await type(edge).find_one({"$and": [{"id": edge.id}]})
                await current_edge.delete()
                process.stdin.write(b"GO\n")
                await process.stdin.drain()
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(process.stdout.readline(), timeout=0.1)
            line = await asyncio.wait_for(process.stdout.readline(), timeout=20)
            assert json.loads(line) == [1, None]
            assert await asyncio.wait_for(process.wait(), timeout=20) == 0
        finally:
            if process.returncode is None:
                process.kill()
            await process.communicate()
