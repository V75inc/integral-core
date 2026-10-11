"""Canonical entry commands retain current reads and nested rollback on PG."""

import asyncio

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.models.nodes import User
from app.services.app_operations.transaction_scope import (
    OperationTransactionUnavailable,
    postgres_graph_transaction,
)
from app.services.entry_write_scope import entry_write_scope
from app.services.permissions import get_user_node
from tests.test_resource_location import graph

pytestmark = [pytest.mark.postgres, pytest.mark.contract, pytest.mark.asyncio]


async def test_entry_scope_serializes_current_principal_reads_and_rolls_back(
    postgres_raw_db,
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, _, _, _, _ = await graph()
        original = owner.display_name
        with pytest.raises(RuntimeError, match="rollback principal mutation"):
            async with entry_write_scope(owner.id) as held:
                first, second = await asyncio.gather(
                    get_user_node(owner.id), get_user_node(owner.id)
                )
                assert first.id == second.id == owner.id
                assert (await first.get_context()).database is held.database
                first.display_name = "must not persist"
                await first.save()
                raise RuntimeError("rollback principal mutation")
        assert (await User.find_one({"id": owner.id})).display_name == original


async def test_caught_entry_failure_poisoned_outer_transaction(postgres_raw_db):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, _, _, _, _ = await graph()
        original = owner.display_name
        with pytest.raises(OperationTransactionUnavailable, match="requires rollback"):
            async with postgres_graph_transaction() as transaction:
                try:
                    async with entry_write_scope(owner.id) as held:
                        assert held.database is transaction
                        current = await get_user_node(owner.id)
                        current.display_name = "must not commit after catch"
                        await current.save()
                        raise RuntimeError("caught inner failure")
                except RuntimeError:
                    pass
        assert (await User.find_one({"id": owner.id})).display_name == original
