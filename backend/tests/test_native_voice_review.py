"""Voice readback accepts only an exact current, readable record proposal."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentive.staging import StagedChange
from app.services import native_voice_review as review


@pytest.mark.asyncio
async def test_entry_review_requires_exact_current_record_and_work(monkeypatch):
    now = datetime.now(timezone.utc)
    change = StagedChange(
        token="private-token",
        user_id="human",
        session_id="thread",
        kind="update_entry",
        summary="Update entry Acme",
        diff_human="title: Acme to Acme Georgetown",
        diff_machine={},
        payload={
            "entry_id": "private-id",
            "title": "Acme Georgetown",
            "expected_record_revision": 2,
        },
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        workspace_id="workspace",
        origin_proof={
            "version": "work-proposal-v1",
            "capability_key": "integral_update_entry",
            "work_item_id": "work",
            "run_id": "run",
            "principal_id": "human",
            "workspace_id": "workspace",
            "thread_id": "thread",
        },
    )
    monkeypatch.setattr(review, "list_pending_tokens", AsyncMock(return_value=[change]))
    monkeypatch.setattr(
        review,
        "locate_resource",
        AsyncMock(
            return_value=SimpleNamespace(workspace_id="workspace", track_id="track")
        ),
    )
    check = AsyncMock(return_value=SimpleNamespace(allowed=True))
    monkeypatch.setattr(review, "evaluate", check)
    from app.services import authority_facts

    row = {"context": {"record_revision": 2, "title": "Acme"}}
    monkeypatch.setattr(
        authority_facts, "primary_node_record", AsyncMock(return_value=row)
    )
    args = dict(
        principal="human",
        workspace="workspace",
        thread="thread",
        work_id="work",
        run_id="run",
    )
    answer = await review.read_entry_review(**args)
    assert answer.text == "Would you like me to rename “Acme” to “Acme Georgetown”?"
    assert "private-token" not in answer.text and "private-id" not in answer.text
    assert await review.read_entry_review(**{**args, "run_id": "other"}) is None
    row["context"]["record_revision"] = 3
    assert await review.read_entry_review(**args) is None
    row["context"]["record_revision"] = 2
    check.return_value = SimpleNamespace(allowed=False)
    assert await review.read_entry_review(**args) is None
    check.return_value = SimpleNamespace(allowed=True)
    change.payload["tags"] = ["other"]
    assert await review.read_entry_review(**args) is None
