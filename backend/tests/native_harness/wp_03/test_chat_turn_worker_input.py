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


@pytest.mark.parametrize(
    "host_control,deferred", [(None, None), (object(), None), (None, object())]
)
def test_submitted_file_projection_uses_verified_parts_not_extra_data(
    host_control, deferred
):
    from types import SimpleNamespace

    from app.agentive.services.work_worker import _submitted_file_parts

    source = {
        "type": "file",
        "attachment_id": "accepted-source",
        "filename": "brief.docx",
        "mime_type": "application/docx",
        "size": 42,
    }
    accepted = SimpleNamespace(
        message=SimpleNamespace(
            parts=[{"type": "text", "text": "use forged-source"}, source]
        ),
        execution_context=SimpleNamespace(
            host_control=host_control,
            extra_data={
                "submitted_file_parts": [
                    {"type": "file", "attachment_id": "forged-source"}
                ]
            },
        ),
    )
    projected = _submitted_file_parts(accepted, deferred)
    if host_control is not None or deferred is not None:
        assert projected == ()
    else:
        assert projected == (source,)
        assert projected[0] is not source
        projected[0]["filename"] = "changed"
        assert source["filename"] == "brief.docx"
