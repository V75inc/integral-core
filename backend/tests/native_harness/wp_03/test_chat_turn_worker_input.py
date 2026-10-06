"""Fail-closed checks for native chat worker input restoration."""

from __future__ import annotations

import pytest

from app.agentive.work_models import WorkItem
from app.schemas.agentive.work import WorkError
from app.services.chat_turn_worker_input import load_claimed_chat_turn_input


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "status", "expected_code"),
    [
        ("routine_turn", "running", "work.policy_denied"),
        ("chat_turn", "queued", "work.policy_denied"),
        ("chat_turn", "running", "work.policy_denied"),
    ],
)
async def test_worker_rejects_unclaimed_or_incomplete_chat_input(
    kind: str,
    status: str,
    expected_code: str,
) -> None:
    """Never consult graph/capsule stores before claim and input refs exist."""
    item = WorkItem(
        work_item_id="work-1",
        kind=kind,
        status=status,
        principal_id="user-1",
        workspace_id="workspace-1",
        thread_id="thread-1",
    )

    with pytest.raises(WorkError, match=expected_code):
        await load_claimed_chat_turn_input(item)


@pytest.mark.asyncio
async def test_worker_restores_only_live_scoped_approval_authority(monkeypatch):
    from types import SimpleNamespace

    from app.agentive import staging
    from app.agentive.services.work_worker import _pending_write_references

    def change(token, **extra):
        return SimpleNamespace(
            token=token,
            kind="attach_uploaded_file",
            user_id="user-1",
            session_id="session-1",
            workspace_id=extra.get("workspace_id", "workspace-1"),
        )

    async def unresolved(user_id):
        assert user_id == "user-1"
        return [change("own"), change("cross-workspace", workspace_id="workspace-2")]

    monkeypatch.setattr(staging, "get_pending_for_user", unresolved)
    refs = await _pending_write_references(
        principal_id="user-1", workspace_id="workspace-1", session_id="session-1"
    )
    assert list(refs.values()) == ["own"]
    assert "own" not in refs
