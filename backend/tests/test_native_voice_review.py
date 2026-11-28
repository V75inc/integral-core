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
    assert answer.speech_text == "Would you like me to rename Acme to Acme Georgetown?"
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


@pytest.mark.parametrize(
    ("spec", "value", "previous", "expected"),
    [
        ({"type": "text"}, "00127", False, "zero, zero, one, two, seven"),
        ({"type": "text"}, "AB-128", False, "AB-128"),
        ({"type": "select", "enum": ["Ready", "Held"]}, "Ready", False, "Ready"),
        ({"type": "select", "enum": ["Ready"]}, "Held", False, None),
        ({"type": "number"}, 0, False, "0"),
        ({"type": "number"}, True, False, None),
        ({"type": "number"}, float("nan"), False, None),
        ({"type": "boolean"}, False, False, "false"),
        ({"type": "boolean"}, "false", False, None),
        ({"type": "date"}, "2026-11-27", False, "2026-11-27"),
        ({"type": "date"}, "2026-02-30", False, None),
        ({"type": "text"}, None, True, "empty"),
        ({"type": "member"}, "someone", False, None),
        ({"type": "relation"}, "some record", False, None),
        ({"type": "file"}, "attachment", False, None),
        ({"type": "computed"}, 5, False, None),
        ({"type": "text", "readonly": True}, "new", False, None),
        ({"type": "text", "composite": {"base": "text"}}, "new", False, None),
        (
            {"type": "text", "validation": {"preview_default_tool": "hook"}},
            "new",
            False,
            None,
        ),
        ({"type": "text"}, "x" * 201, False, None),
    ],
)
def test_simple_field_projection_is_schema_bound_and_literal(
    spec, value, previous, expected
):
    assert review._simple_field_text(spec, value, previous=previous) == expected


@pytest.mark.parametrize("literal", ['Alpha "Beta"', "Alpha “Beta”", "Alpha ”Beta“"])
def test_spoken_review_preserves_literal_quotation_and_meaningful_punctuation(literal):
    action = f"change “{literal}” from -12.50 to 25.00"
    assert review.spoken_review_action(action, literal) == action
    assert (
        review.spoken_review_action("change “Alpha” from -12.50 to 25.00", "Alpha")
        == "change Alpha from -12.50 to 25.00"
    )
