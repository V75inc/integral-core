"""Common workspace fence for membership writers and departure cleanup."""

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import AsyncIterator

from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError
from app.middleware.permissions_cache import isolated_permissions_cache
from app.services.app_operations.transaction_scope import (
    graph_transaction_available,
    postgres_graph_transaction,
)
from app.services.authorization_scope import authorization_scope
from app.services.entry_write_scope import entry_write_scope

_active: ContextVar[tuple[str, object, object] | None] = ContextVar(
    "integral_membership_write_scope", default=None
)


@asynccontextmanager
async def membership_write_scope(workspace_id: str) -> AsyncIterator[None]:
    """Order member changes with participating workspace authority writers."""
    database = get_default_context().database
    active = _active.get()
    if active is not None:
        if (
            active[0] != workspace_id
            or active[1] is not asyncio.current_task()
            or active[2] is not database
        ):
            raise AuthorizationUnavailableError()
        yield
        return
    with isolated_permissions_cache():
        if graph_transaction_available(database):
            async with (
                postgres_graph_transaction(database),
                authorization_scope(workspace_id, authority_mutation=True),
            ):
                token = _active.set(
                    (
                        workspace_id,
                        asyncio.current_task(),
                        get_default_context().database,
                    )
                )
                try:
                    yield
                finally:
                    _active.reset(token)
        else:
            async with entry_write_scope(f"workspace-membership:{workspace_id}"):
                token = _active.set(
                    (
                        workspace_id,
                        asyncio.current_task(),
                        get_default_context().database,
                    )
                )
                try:
                    yield
                finally:
                    _active.reset(token)
