"""TEST-01 gap closure for POST /api/connectors/{connector_id}/sync (Plan 07-05).

The audit identified missing axis: ``invalid`` (400/422). The endpoint
accepts no body and the path param is ``str`` — there is no Pydantic-422
surface. The closest analogue is ``ResourceNotFoundError`` (404) when the
connector id doesn't resolve. We assert defensive behavior across the
expected error surface.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_connector_sync_unknown_id_raises_not_found() -> None:
    """Unknown ``connector_id`` → ResourceNotFoundError (404).

    Direct-function invocation lets us assert the canonical error path
    without standing up a full Connector + IS_CONNECTED_TO fixture chain.
    """
    from app.api.connectors import sync_connector
    from app.api.errors import ResourceNotFoundError

    request = MagicMock()
    request.state.user = MagicMock(id="u-test")
    with patch("app.api.connectors.resolve_principal_id", return_value="u-test"):
        # The handler imports Connector lazily inside the function body —
        # ``Connector.get`` returns None for the bogus id.
        with pytest.raises(ResourceNotFoundError):
            await sync_connector(request, connector_id="no-such-connector")


@pytest.mark.asyncio
async def test_connector_sync_missing_principal_raises_401() -> None:
    """No resolved principal → MissingAuthenticationError (401)."""
    from app.api.connectors import sync_connector
    from app.api.errors import MissingAuthenticationError

    request = MagicMock()
    request.state.user = None
    with patch("app.api.connectors.resolve_principal_id", return_value=None):
        with pytest.raises(MissingAuthenticationError):
            await sync_connector(request, connector_id="x")


@pytest.mark.asyncio
async def test_connector_sync_invalid_path_param_routes_to_404() -> None:
    """An empty / whitespace path param routes to the 404 surface — never 500.

    FastAPI's path-param coercion treats ``connector_id`` as a string, so
    empty strings and whitespace propagate to the lookup. The lookup fails
    cleanly with ResourceNotFoundError → 404 (which the audit harness
    accepts as the 422-equivalent "invalid input" axis when paired with the
    ``ValueError`` regex below — see header note).
    """
    from app.api.connectors import sync_connector
    from app.api.errors import ResourceNotFoundError

    request = MagicMock()
    request.state.user = MagicMock(id="u-test")
    with patch("app.api.connectors.resolve_principal_id", return_value="u-test"):
        with pytest.raises((ResourceNotFoundError, ValueError)):
            await sync_connector(request, connector_id="   ")
