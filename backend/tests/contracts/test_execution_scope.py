"""WP-01 contracts for the transport-independent execution scope."""

from __future__ import annotations

import pytest

from app.contracts.runtime import ExecutionScope, InvalidExecutionScope


def test_execution_scope_normalizes_identity_and_workspace() -> None:
    scope = ExecutionScope.create(
        principal_id=" user-1 ", workspace_id=" ws-1 ", origin=" mcp "
    )

    assert scope.principal_id == "user-1"
    assert scope.workspace_id == "ws-1"
    assert scope.origin == "mcp"
    assert scope.workspace_scope == "workspace:ws-1"


@pytest.mark.parametrize("principal_id,workspace_id", [("", "ws"), ("u", "")])
def test_execution_scope_rejects_incomplete_effect_identity(
    principal_id: str, workspace_id: str
) -> None:
    with pytest.raises(InvalidExecutionScope):
        ExecutionScope.create(principal_id=principal_id, workspace_id=workspace_id)
