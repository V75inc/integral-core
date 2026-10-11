"""Common workspace fence for membership writers and departure cleanup."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from jvspatial.core.context import get_default_context

from app.middleware.permissions_cache import isolated_permissions_cache
from app.services.app_operations.transaction_scope import (
    graph_transaction_available,
    postgres_graph_transaction,
)
from app.services.authorization_scope import authorization_scope
from app.services.entry_write_scope import entry_write_scope


@asynccontextmanager
async def membership_write_scope(workspace_id: str) -> AsyncIterator[None]:
    """Order member changes with participating workspace authority writers."""
    database = get_default_context().database
    with isolated_permissions_cache():
        if graph_transaction_available(database):
            async with (
                postgres_graph_transaction(database),
                authorization_scope(workspace_id, authority_mutation=True),
            ):
                yield
        else:
            async with entry_write_scope(f"workspace-membership:{workspace_id}"):
                yield
