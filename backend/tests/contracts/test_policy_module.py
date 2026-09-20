"""WP-01 contract for the identity/policy module seam."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.contracts.runtime import ExecutionScope
from app.modules.policy import PolicyModule
from app.schemas.policy import Decision, Resource


@pytest.mark.asyncio
async def test_policy_module_delegates_with_the_immutable_execution_scope() -> None:
    scope = ExecutionScope.create(principal_id="user-1", workspace_id="ws-1")
    resource = Resource(kind="app", id="n.App.one", scope="app:n.App.one")
    expected = Decision(allowed=True, reason="contract")

    with patch(
        "app.modules.policy.evaluate", new=AsyncMock(return_value=expected)
    ) as evaluate:
        actual = await PolicyModule().evaluate(
            scope=scope, action="app.read", resource=resource
        )

    assert actual is expected
    assert evaluate.await_args.kwargs["subject"].id == "user-1"
    assert evaluate.await_args.kwargs["action"] == "app.read"
    assert evaluate.await_args.kwargs["resource"] is resource
