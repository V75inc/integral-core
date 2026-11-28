"""Single-record deletion uses deliberate independent speech and canonical receipts."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentive.services.approval_decisions import decide_staged_write
from app.agentive.services.proposal_origin import bind_proposal_origin
from app.agentive.services.work_approvals import propose_work_approval_unit
from app.agentive.services.work_items import claim_due_candidate
from app.agentive.staging import StagingError, create_staged_change, get_token
from app.models.nodes import Entry
from app.schemas.agentive.work import (
    ChatTurnExecutionContext,
    ChatTurnSubmissionRequest,
)
from app.services import native_voice
from app.services.chat_threads import create_thread
from app.services.chat_turn_submissions import submit_chat_turn
from app.services.native_voice_confirmation import VoiceConfirmation
from app.services.resident_permissions import apply_resident_permission
from tests.test_native_voice import Browser, Provider

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


@pytest.mark.parametrize(
    "case", ["approve", "reject", "ambiguous", "changed", "duplicate", "anchored"]
)
async def test_leaf_delete_deliberate_voice_confirmation(
    authenticated_client, test_user, monkeypatch, case
):
    if os.getenv("INTEGRAL_TEST_DB") not in ("postgres", "postgresql"):
        pytest.skip("Requires PostgreSQL")
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "29" * 32)
    created = await authenticated_client.post(
        "/api/workspaces", json={"name": "Specimen disposal test"}
    )
    assert created.status_code == 200
    ws = created.json()["workspace"]["id"]
    me = test_user.user_id
    headers = {"X-Integral-Scope": f"ws:{ws}"}
    app = await authenticated_client.post(
        "/api/apps",
        headers=headers,
        json={"name": "Herbarium", "visibility": "private"},
    )
    assert app.status_code == 200, app.text
    app_id = app.json()["app"]["id"]
    track = await authenticated_client.post(
        "/api/tracks",
        headers=headers,
        json={"title": "Samples", "visibility": "private", "app_id": app_id},
    )
    assert track.status_code in (200, 201), track.text
    track_id = track.json()["track"]["id"]
    row = await authenticated_client.post(
        "/api/entries",
        headers=headers,
        json={
            "track_id": track_id,
            "title": "Juniper specimen",
            "body": "Synthetic fixture only",
        },
    )
    assert row.status_code in (200, 201), row.text
    entry = row.json()["entry"]
    if case == "duplicate":
        another = await authenticated_client.post(
            "/api/entries",
            headers=headers,
            json={
                "track_id": track_id,
                "title": "JUNIPER SPECIMEN",
                "body": "A separate synthetic record",
            },
        )
        assert another.status_code in (200, 201), another.text
    if case == "anchored":
        from app.models.edges import ANCHORS
        from app.models.nodes import Track

        another = await authenticated_client.post(
            "/api/tracks",
            headers=headers,
            json={
                "title": "Attached details",
                "visibility": "private",
                "app_id": app_id,
            },
        )
        assert another.status_code in (200, 201), another.text
        parent = await Entry.get(entry["id"])
        child = await Track.get(another.json()["track"]["id"])
        await parent.connect(child, edge=ANCHORS)
    grant = await authenticated_client.put(
        f"/api/workspaces/{ws}/resident-permission",
        headers=headers,
        json={
            "mode": "custom",
            "revision": "",
            "track_ids": [track_id],
            "app_ids": [app_id],
        },
    )
    assert grant.status_code == 200, grant.text
    thread = await create_thread(
        user_id=me, workspace_id=ws, provider_id="integral_native"
    )
    receipt = await submit_chat_turn(
        ChatTurnSubmissionRequest(
            principal_id=me,
            workspace_id=ws,
            thread_id=thread.id,
            client_request_id="voice-leaf-delete-fixture",
            parts=[{"type": "text", "text": "Remove the Juniper specimen"}],
            execution_context=ChatTurnExecutionContext(
                extra_data={"voice_delegate": True}
            ),
        )
    )
    claimed = await claim_due_candidate(
        worker_id="voice-leaf-delete",
        work_item_id=receipt.work_item_id,
        lease_seconds=60,
    )
    run_id = "voice-delete-run"
    payload = {
        "entry_id": entry["id"],
        "expected_record_revision": entry["record_revision"],
    }
    proof = {
        "version": "work-proposal-v1",
        "capability_key": "integral_delete_entry",
        "work_item_id": claimed.work_item_id,
        "run_id": run_id,
        "principal_id": me,
        "workspace_id": ws,
        "thread_id": thread.id,
        "attempt": claimed.attempt,
        "effect_key": "delete-fixture",
    }
    with bind_proposal_origin(proof):
        staged = await create_staged_change(
            user_id=me,
            workspace_id=ws,
            session_id=thread.id,
            kind="delete_entry",
            summary="Delete record",
            diff_human="Permanent deletion",
            diff_machine=payload,
            payload=payload,
        )
    _, parked = await propose_work_approval_unit(
        work_item_id=claimed.work_item_id,
        lease_token=claimed.lease_token,
        lease_fence=claimed.lease_fence,
        run_id=run_id,
        run_step_id="delete-step",
        staging_token=staged.token,
        authority_digest="fixture",
    )
    assert parked.status == "waiting_for_human"
    ctx = SimpleNamespace(
        attempt=claimed.attempt,
        effect_key="delete-fixture",
        principal_id=me,
        workspace_id=ws,
        thread_id=thread.id,
        work_item_id=claimed.work_item_id,
        run_id=run_id,
    )
    assert await apply_resident_permission(claimed, ctx, staged.token) is None
    with pytest.raises(StagingError):
        await decide_staged_write(
            principal_id=me,
            workspace_id=ws,
            conversation_id=thread.id,
            proposal_id=staged.token,
            decision="approve",
            source="voice",
            strong_confirmation=True,
        )
    assert await Entry.get(entry["id"]) is not None
    relay = native_voice.VoiceRelay(
        Browser(),
        Provider(),
        native_voice.VoiceScope(me, ws, "", thread.id),
        conversational_review=True,
    )
    relay.accepted = native_voice._AcceptedVoiceTurn(
        receipt.work_item_id, receipt.message_id, "Remove the Juniper specimen"
    )
    review = await relay._current_review()
    if case in {"duplicate", "anchored"}:
        assert review is None
        return
    assert (
        review
        and "Juniper specimen" in review.text
        and "cannot be undone" in review.text
        and "Samples" in review.text
    )
    if case == "changed":
        updated = await authenticated_client.put(
            f'/api/entries/{entry["id"]}',
            headers=headers,
            json={"title": "Moved specimen"},
        )
        assert updated.status_code == 200, updated.text
    from app.agentive.services.speech import service

    confirmation = VoiceConfirmation(review, ("fixture", "model", "byok", "fixture"))
    confirmation.phase = "capture"
    confirmation.audio.extend(b"\xff\x7f" * 48000)
    confirmation.capture_done.set()
    relay.review_confirmation = confirmation
    monkeypatch.setattr(
        service,
        "transcribe_voice_confirmation",
        AsyncMock(
            return_value={
                "approve": "Yes, delete it",
                "reject": "No, thank you",
                "ambiguous": "Yes",
                "changed": "Yes, delete it",
            }[case]
        ),
    )
    await relay._capture_review_confirmation(confirmation)
    assert (await get_token(staged.token)).state == {
        "approve": "consumed",
        "reject": "revoked",
        "ambiguous": "pending",
        "changed": "pending",
    }[case]
    from jvspatial.core.context import get_default_context

    from app.services.authority_facts import primary_node_record

    assert (
        await primary_node_record(get_default_context().database, Entry, entry["id"])
        is None
    ) == (case == "approve")
    if case == "approve":
        from app.services.native_voice_deletion import read_saved_deletion

        assert await read_saved_deletion(review, me, ws) is not None
