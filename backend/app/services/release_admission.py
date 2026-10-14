"""Short current-authority admission before handing bytes to a transport."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from app.middleware.permissions_cache import isolated_permissions_cache


@asynccontextmanager
async def release_admission(
    principal_id: str, workspace_id: str | None
) -> AsyncIterator[None]:
    """Order admission with participating revocation writers, then release locks.

    The protected interval ends before the caller sends/yields network bytes.
    Revocation after this admission cannot recall that already-admitted frame;
    every later frame must obtain a new admission. No transaction spans a send.
    """
    from app.api.errors import InsufficientPermissionsError
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
        postgres_graph_transaction,
    )
    from app.services.authorization_scope import authorization_scope
    from app.services.identity_admission import identity_admission
    from app.services.workspace_permissions import can_access_workspace

    with isolated_permissions_cache():
        if not graph_transaction_available():
            yield  # The legacy local adapter cannot claim distributed ordering.
            return
        async with postgres_graph_transaction(), identity_admission((principal_id,)):
            if workspace_id is None:
                yield  # Immutable own-account audiences still require live identity.
                return
            async with authorization_scope(workspace_id):
                if await can_access_workspace(principal_id, workspace_id) == "none":
                    raise InsufficientPermissionsError(
                        message="Workspace access revoked"
                    )
                yield
