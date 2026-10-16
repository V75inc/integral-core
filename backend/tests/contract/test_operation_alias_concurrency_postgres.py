"""Independent PostgreSQL alias claims serialize old/new workers and cancellation."""

import asyncio
from contextvars import Context

import pytest
from jvspatial.core.context import (
    GraphContext,
    get_default_context,
    scoped_default_context_async,
)

from app.models.nodes import Entry
from app.schemas.policy import Subject
from app.services.app_operations.execution_receipts import (
    OperationReceiptIncomplete,
    execute_operation_once,
    receipt_object_id,
)
from app.services.app_operations.principal import resolve_operation_principal
from app.services.authority_facts import primary_node_record
from app.services.permissions import get_user_node
from tests.contract.test_access_delegation_postgres import second_database
from tests.contract.test_operation_nested_result_postgres import operation_context
from tests.contract.test_operation_parent_result_postgres import child_reference
from tests.contract.test_operation_principal_alias_postgres import alias_identity

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


@pytest.mark.parametrize("first_worker", ["current_auth", "legacy_graph"])
async def test_opposing_alias_workers_share_the_original_receipt_claim(
    postgres_raw_db, first_worker
):
    other_database = await second_database()
    tasks = []
    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            actor, workspace, app, visible, _ = await operation_context(role="editor")
            profile = await get_user_node(actor.id)
            preferred = alias_identity(actor, workspace, app, actor.id)
            legacy = alias_identity(actor, workspace, app, profile.id)
            entered, release, other_started = (
                asyncio.Event(),
                asyncio.Event(),
                asyncio.Event(),
            )
            calls = 0
            child_id = ""

            async def effect():
                nonlocal calls, child_id
                calls += 1
                graph = get_default_context()
                row = await primary_node_record(graph.database, Entry, visible.id)
                parent = await graph._deserialize_entity(Entry, row)
                child = await child_reference("comment", parent, actor)
                child_id = child.id
                entered.set()
                await release.wait()
                return {"output": {"child": child.id}}

            async def run(database, legacy_worker, announce=False):
                async with scoped_default_context_async(
                    GraphContext(database=database)
                ):
                    if announce:
                        other_started.set()
                    principal = await resolve_operation_principal(
                        Subject(kind="human", id=profile.id)
                    )
                    return await execute_operation_once(
                        identity=legacy if legacy_worker else preferred,
                        principal_aliases=() if legacy_worker else principal.aliases,
                        admit_identity=None if legacy_worker else principal.admit,
                        request_hash="same-original-input",
                        execute=effect,
                    )

            old_first = first_worker == "legacy_graph"
            first = asyncio.create_task(
                run(postgres_raw_db, old_first), context=Context()
            )
            tasks.append(first)
            await asyncio.wait_for(entered.wait(), timeout=15)
            second = asyncio.create_task(
                run(other_database, not old_first, True), context=Context()
            )
            tasks.append(second)
            await asyncio.wait_for(other_started.wait(), timeout=15)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(second), timeout=0.15)
            assert calls == 1
            release.set()
            original = await asyncio.wait_for(first, timeout=15)
            if old_first:
                replay = await asyncio.wait_for(second, timeout=15)
                assert replay.replayed and replay.identity == legacy
                assert original.identity == replay.identity
                assert original.result == replay.result
            else:
                with pytest.raises(OperationReceiptIncomplete):
                    await asyncio.wait_for(second, timeout=15)
                assert original.identity == preferred
            assert calls == 1 and child_id
            assert await postgres_raw_db.get("node", child_id) is not None
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await other_database.close()


async def test_cancelled_alias_operation_rolls_back_both_claims_and_local_child(
    postgres_raw_db,
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, visible, _ = await operation_context(role="editor")
        profile = await get_user_node(actor.id)
        preferred = alias_identity(actor, workspace, app, actor.id)
        legacy = alias_identity(actor, workspace, app, profile.id)
        principal = await resolve_operation_principal(
            Subject(kind="human", id=actor.id)
        )
        child_id = ""

        async def cancelled():
            nonlocal child_id
            graph = get_default_context()
            row = await primary_node_record(graph.database, Entry, visible.id)
            parent = await graph._deserialize_entity(Entry, row)
            child = await child_reference("comment", parent, actor)
            child_id = child.id
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await execute_operation_once(
                identity=preferred,
                principal_aliases=principal.aliases,
                admit_identity=principal.admit,
                request_hash="input",
                execute=cancelled,
            )
        assert child_id and await postgres_raw_db.get("node", child_id) is None
        for identity in (preferred, legacy):
            assert (
                await postgres_raw_db.get("object", receipt_object_id(identity)) is None
            )
