"""Resident tool policy checks use the Core execution-scope contract."""

from __future__ import annotations

import pytest

from app.agentive.tooling.policy_gate import enforce_tool_policy
from app.schemas.policy import Decision


class _PointReadSpec:
    op_class = "read"
    policy_action = "entry.read"


@pytest.mark.asyncio
async def test_resident_policy_receives_bound_execution_scope(monkeypatch):
    """A resident point check receives validated transport-independent identity."""
    seen = {}

    async def allow(**kwargs):
        seen.update(kwargs)
        return Decision(allowed=True, reason="allowed")

    monkeypatch.setattr("app.agentive.tooling.policy_gate.policy_evaluate", allow)

    result = await enforce_tool_policy(
        _PointReadSpec(),
        {"entry_id": "n.Entry.1"},
        principal_id=" user-1 ",
        workspace_id=" workspace-1 ",
    )

    assert result is None
    assert seen["execution_scope"].principal_id == "user-1"
    assert seen["execution_scope"].workspace_id == "workspace-1"
    assert seen["execution_scope"].origin == "resident_tool"


@pytest.mark.asyncio
async def test_resident_policy_rejects_point_check_without_workspace():
    """A resident point check cannot substitute a missing workspace."""
    result = await enforce_tool_policy(
        _PointReadSpec(),
        {"entry_id": "n.Entry.1"},
        principal_id="user-1",
    )

    assert result is not None
    assert result.is_error
    assert result.error_code == "invalid_execution_scope"
