"""WP-01 contract for the identity/policy module seam."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.contracts.runtime import ExecutionScope
from app.modules.policy import PolicyModule
from app.schemas.policy import Decision, Resource


@pytest.mark.asyncio
async def test_policy_module_delegates_with_the_immutable_execution_scope() -> None:
    """Public policy seam preserves the validated scope principal."""
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


def test_policy_revision_changes_when_effective_policy_changes() -> None:
    """Policy-bound outputs have an explicit invalidation token."""
    scope = ExecutionScope.create(principal_id="user-1", workspace_id="ws-1")
    resource = Resource(kind="app", id="app-1", scope="app:app-1")
    module = PolicyModule()
    before = module.revision(
        scope=scope,
        action="app.read",
        resource=resource,
        decision=Decision(allowed=True, reason="default_human_policy"),
    )
    after = module.revision(
        scope=scope,
        action="app.read",
        resource=resource,
        decision=Decision(allowed=False, reason="default_human_policy_denied"),
    )

    assert before != after
