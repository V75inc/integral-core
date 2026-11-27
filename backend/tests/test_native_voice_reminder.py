"""A spoken decision authorizes only the exact one-shot reminder read to its owner."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentive.services import approval_decisions
from app.agentive.staging import StagedChange, StagingError
from app.services import native_voice_reminder as reminder
from app.services import native_voice_review as review


@pytest.fixture
def change():
    now = datetime.now(timezone.utc)
    return StagedChange(
        token="proposal",
        user_id="caller",
        workspace_id="workspace",
        session_id="thread",
        kind="routine_task_create",
        summary="Reminder",
        diff_human="Untrusted summary",
        diff_machine={},
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        payload=dict(
            thread_id="thread",
            workspace_id="workspace",
            agent_id="integral_core",
            instruction="Check the compass.",
            timezone="America/Guyana",
            run_at="2099-01-15T13:00:00+00:00",
            max_runs=1,
            cron="",
            delivery_mode="reminder",
            write_scope=[],
        ),
        origin_proof=dict(
            version="work-proposal-v1",
            capability_key="integral_schedule_task",
            work_item_id="work",
            run_id="run",
            principal_id="caller",
            workspace_id="workspace",
            thread_id="thread",
        ),
    )


async def project():
    return await review.read_entry_review(
        principal="caller",
        workspace="workspace",
        thread="thread",
        work_id="work",
        run_id="run",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key,value",
    [
        ("delivery_mode", "agent"),
        ("write_scope", ["track"]),
        ("thread_id", "other"),
        ("workspace_id", "other"),
        ("run_at", "yesterday"),
        ("timezone", "unknown"),
        ("cron", "* * * * *"),
        ("max_runs", True),
        ("watch_goal", True),
        ("instruction", "two\nlines"),
        ("instruction", "x" * 301),
    ],
)
async def test_other_effects_are_not_spoken_reminders(change, monkeypatch, key, value):
    change.payload[key] = value
    monkeypatch.setattr(review, "list_pending_tokens", AsyncMock(return_value=[change]))
    assert await project() is None


@pytest.mark.asyncio
async def test_exact_review_and_decision(change, monkeypatch):
    monkeypatch.setattr(review, "list_pending_tokens", AsyncMock(return_value=[change]))
    spoken = await project()
    assert "January 15, 2099 at 9 AM" in spoken.text
    assert "Check the compass." in spoken.text and "in this chat" in spoken.text
    assert "Untrusted summary" not in spoken.text
    monkeypatch.setattr(approval_decisions, "get_token", AsyncMock(return_value=change))
    apply = AsyncMock(return_value={"consumed": True})
    monkeypatch.setattr(approval_decisions, "bless_and_execute", apply)

    async def decide():
        return await approval_decisions.decide_staged_write(
            principal_id="caller",
            workspace_id="workspace",
            conversation_id="thread",
            proposal_id="proposal",
            decision="approve",
            source="voice",
        )

    with pytest.raises(StagingError):
        await decide()
    with reminder.confirmed_reminder(spoken):
        change.payload["instruction"] = "Changed."
        with pytest.raises(StagingError):
            await decide()
        apply.assert_not_awaited()
        change.payload["instruction"] = "Check the compass."
        assert (await decide())["consumed"]
    assert not reminder.permits_reminder(change)
    apply.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconnect_uses_only_single_scoped_pending_reminder(change, monkeypatch):
    from app.agentive import staging

    item = dict(
        kind="staged_write",
        write_kind=change.kind,
        status="pending",
        token=change.token,
    )
    thread = SimpleNamespace(
        id="thread",
        user_id="caller",
        workspace_id="workspace",
        prompt_queue=dict(status="open", items=[item]),
    )
    monkeypatch.setattr(staging, "get_token", AsyncMock(return_value=change))
    assert await review.pending_review_work(thread, "caller", "workspace") == (
        "work",
        change.token,
    )
    assert await review.pending_review_work(thread, "other", "workspace") is None
    thread.prompt_queue["items"].append(deepcopy(item))
    assert await review.pending_review_work(thread, "caller", "workspace") is None
    thread.prompt_queue["items"].pop()
    change.origin_proof["thread_id"] = "other"
    assert await review.pending_review_work(thread, "caller", "workspace") is None


@pytest.mark.asyncio
async def test_saved_readback_revalidates_receipt_and_current_schedule(
    change, monkeypatch
):
    from contextlib import nullcontext

    from app.agentive.services import staging_execution, staging_routine_commands
    from app.services import release_admission

    spoken = reminder.reminder_review(change, "work", "run")
    p = change.payload
    row = SimpleNamespace(
        **p,
        next_run_at=p["run_at"],
        status="active",
        source_staging_token=change.token,
        user_id=change.user_id,
    )
    receipt = AsyncMock(return_value={"routine_id": "saved"})
    monkeypatch.setattr(staging_execution, "load_staging_execution_result", receipt)
    monkeypatch.setattr(
        release_admission, "release_admission", lambda *args: nullcontext()
    )
    read = AsyncMock(return_value=row)
    monkeypatch.setattr(staging_routine_commands, "admit_owned_routine", read)
    result = await review.read_applied_values(
        spoken, principal="caller", workspace="workspace"
    )
    assert result.text == "Your reminder is scheduled for this chat."
    read.assert_awaited_once_with("caller", "workspace", "saved")
    row.next_run_at = "changed"
    assert (
        await review.read_applied_values(
            spoken, principal="caller", workspace="workspace"
        )
        is None
    )
    receipt.return_value = {}
    read.reset_mock()
    assert (
        await review.read_applied_values(
            spoken, principal="caller", workspace="workspace"
        )
        is None
    )
    read.assert_not_awaited()


@pytest.mark.asyncio
async def test_verbal_rejection_revokes_only_confirmed_proposal(change, monkeypatch):
    spoken = reminder.reminder_review(change, "work", "run")
    revoke = AsyncMock(return_value=change)
    apply = AsyncMock()
    monkeypatch.setattr(approval_decisions, "get_token", AsyncMock(return_value=change))
    monkeypatch.setattr(approval_decisions, "revoke_token", revoke)
    monkeypatch.setattr(approval_decisions, "bless_and_execute", apply)
    with reminder.confirmed_reminder(spoken):
        result = await approval_decisions.decide_staged_write(
            principal_id="caller",
            workspace_id="workspace",
            conversation_id="thread",
            proposal_id="proposal",
            decision="reject",
            source="voice",
        )
    assert result["ok"] and result["decision"] == "reject"
    revoke.assert_awaited_once_with(
        user_id="caller", token="proposal", decision_source="voice"
    )
    apply.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_decision_fences_order_payload_and_origin(change, monkeypatch):
    from app.services import native_voice_batch as batch

    parent = deepcopy(change)
    parent.kind = "batch"
    parent.origin_proof["capability_key"] = "integral_commit_batch"
    parent.payload = {
        "operations": [
            {
                "kind": "create_entry",
                "payload": {"title": "Book repair"},
                "diff_machine": {},
                "diff_human": "ignore",
            },
            {"kind": change.kind, "payload": change.payload},
        ]
    }
    monkeypatch.setattr(
        batch,
        "read_creation_review",
        AsyncMock(
            return_value=review.VoiceReviewReadout(
                "add “Book repair” to Repairs",
                "work",
                "run",
                "proposal:step:0",
                "track",
                "fingerprint",
                creation={"title": "Book repair", "body": "", "fields": {}},
                destination_label="Repairs",
            )
        ),
    )
    offer = await batch.read_batch_review(parent, "work", "run")
    assert offer and "Book repair" in offer.text
    assert not batch.permits_batch(parent)
    with batch.confirmed_batch(offer):
        assert batch.permits_batch(parent)
        for key, value in [
            ("thread_id", "another-thread"),
            ("instruction", "different"),
            ("write_scope", ["*"]),
        ]:
            edited = deepcopy(parent)
            edited.payload["operations"][1]["payload"][key] = value
            assert not batch.permits_batch(edited)
        edited = deepcopy(parent)
        edited.payload["operations"].reverse()
        assert not batch.permits_batch(edited)
    for kind, payload in [
        ("delete_entry", {}),
        ("routine_task_create", {**change.payload, "cron": "0 1 * * *"}),
        ("create_entry", {"title": "$batch.0.id"}),
    ]:
        replacement = deepcopy(parent)
        replacement.payload["operations"][1 if kind != "create_entry" else 0] = {
            "kind": kind,
            "payload": payload,
        }
        assert await batch.read_batch_review(replacement, "work", "run") is None
