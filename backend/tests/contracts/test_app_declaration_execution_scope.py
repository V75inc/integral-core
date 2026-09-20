"""WP-01: declared App reads reject malformed execution identity before lookup."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.api.errors import BadRequestError
from app.services.app_operations.dispatch import list_app_operations
from app.services.app_queries.dispatch import list_app_queries


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id,workspace_id", [("", "ws-1"), ("u-1", "")])
async def test_operation_declaration_read_rejects_incomplete_scope(
    user_id: str, workspace_id: str
) -> None:
    """A declaration read cannot infer a missing user or workspace."""
    with patch(
        "app.services.app_operations.dispatch.App.get", new=AsyncMock()
    ) as app_get:
        with pytest.raises(BadRequestError, match="no active workspace"):
            await list_app_operations(
                user_id=user_id, workspace_id=workspace_id, app_id="n.App.one"
            )
    app_get.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id,workspace_id", [("", "ws-1"), ("u-1", "")])
async def test_query_declaration_read_rejects_incomplete_scope(
    user_id: str, workspace_id: str
) -> None:
    """Query declarations use the same explicit scope requirement."""
    with patch("app.services.app_queries.dispatch.App.get", new=AsyncMock()) as app_get:
        with pytest.raises(BadRequestError, match="no active workspace"):
            await list_app_queries(
                user_id=user_id, workspace_id=workspace_id, app_id="n.App.one"
            )
    app_get.assert_not_awaited()
