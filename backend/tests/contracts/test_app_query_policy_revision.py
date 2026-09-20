"""Query results carry the authorization state that governed their read."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.policy import Decision
from app.services.app_queries.dispatch import invoke_app_query


@pytest.mark.contract
@pytest.mark.asyncio
async def test_query_result_carries_current_policy_revision() -> None:
    """A query response makes its evaluated policy state inspectable."""
    workspace_id = "ws-query-policy"
    app_id = "n.App.query-policy"
    app = type(
        "AppStub",
        (),
        {"id": app_id, "workspace_id": workspace_id, "lifecycle_state": "active"},
    )()

    async def handler(_params, _context):
        return {"count": 1}

    with (
        patch(
            "app.services.app_queries.dispatch.App.get",
            new=AsyncMock(return_value=app),
        ),
        patch(
            "app.services.app_queries.dispatch.can_access_workspace",
            new=AsyncMock(return_value="owner"),
        ),
        patch(
            "app.services.app_queries.dispatch.resolve_role",
            new=AsyncMock(return_value="owner"),
        ),
        patch(
            "app.services.app_queries.dispatch.get_app_query",
            return_value={
                "key": "count",
                "policy_action": "app.read",
                "handler_ref": "test.query_handler",
                "input_schema": {},
            },
        ),
        patch(
            "app.services.app_queries.dispatch.policy_evaluate",
            new=AsyncMock(
                return_value=Decision(allowed=True, reason="default_human_policy")
            ),
        ),
        patch(
            "app.services.app_queries.dispatch.resolve_handler", return_value=handler
        ),
    ):
        result = await invoke_app_query(
            user_id="user-1",
            workspace_id=workspace_id,
            app_id=app_id,
            query_key="count",
        )

    assert result["output"] == {"count": 1}
    assert result["policy_revision"].startswith("policy-sha256:")
