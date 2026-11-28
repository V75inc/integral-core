"""Explicit App presentation grants and independent spoken decisions."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentive.services.proposal_origin import bind_proposal_origin
from app.agentive.services.work_approvals import propose_work_approval_unit
from app.agentive.services.work_items import claim_due_candidate
from app.agentive.staging import create_staged_change, get_token
from app.models.nodes import App, Dashboard
from app.schemas.agentive.work import (
    ChatTurnExecutionContext,
    ChatTurnSubmissionRequest,
)
from app.services import native_voice
from app.services.app_home import home_revision_token
from app.services.chat_threads import create_thread
from app.services.chat_turn_submissions import submit_chat_turn
from app.services.dashboard_service import create_dashboard
from app.services.native_voice_confirmation import VoiceConfirmation
from app.services.resident_permissions import apply_resident_permission
from tests.test_native_voice import Browser, Provider

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


@pytest.mark.parametrize("surface", ["dashboard", "home"])
@pytest.mark.parametrize(
    "case",
    ["auto_typed", "auto_voice", "other_app", "ask", "approve", "reject", "stale"],
)
async def test_presentation_dashboard_permission(
    authenticated_client, test_user, monkeypatch, case, surface
):
    if os.getenv("INTEGRAL_TEST_DB") not in ("postgres", "postgresql"):
        pytest.skip("Requires isolated PostgreSQL")
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "29" * 32)
    created = await authenticated_client.post(
        "/api/workspaces", json={"name": "Botanical research"}
    )
    assert created.status_code == 200
    ws = created.json()["workspace"]["id"]
    me = test_user.user_id
    headers = {"X-Integral-Scope": f"ws:{ws}"}
    ids = []
    for name in ("Specimens", "Field trips"):
        created = await authenticated_client.post(
            "/api/apps", headers=headers, json={"name": name, "visibility": "private"}
        )
        assert created.status_code == 200, created.text
        ids.append(created.json()["app"]["id"])
    app_id = ids[0]
    board = await create_dashboard(user_id=me, app_id=app_id, name="Specimen summary")
    if surface == "home":
        from tests.test_app_home_override import home

        response = await authenticated_client.put(
            f"/api/apps/{app_id}",
            headers=headers,
            json={"home": {**home(), "title": "Specimen summary"}},
        )
        assert response.status_code == 200, response.text
    app = await App.get(app_id)

    async def read_title():
        if surface == "home":
            return (await App.get(app_id)).home_composition["title"]
        return (await Dashboard.get(board.id)).name

    path = f"/api/workspaces/{ws}/resident-permission"
    response = await authenticated_client.put(
        path,
        headers=headers,
        json={
            "mode": "custom",
            "revision": "",
            "track_ids": [],
            "app_ids": [ids[1] if case == "other_app" else app_id],
        },
    )
    assert response.status_code == 200, response.text
    grant = response.json()
    if case in {"ask", "approve", "reject"}:
        response = await authenticated_client.put(
            path, headers=headers, json={"mode": "ask", "revision": grant["revision"]}
        )
        assert response.status_code == 200 and response.json()["custom_app_ids"] == [
            app_id
        ]
    thread = await create_thread(
        user_id=me, workspace_id=ws, provider_id="integral_native"
    )
    receipt = await submit_chat_turn(
        ChatTurnSubmissionRequest(
            principal_id=me,
            workspace_id=ws,
            thread_id=thread.id,
            client_request_id="presentation-fixture",
            parts=[{"type": "text", "text": "Rename the specimen summary"}],
            execution_context=ChatTurnExecutionContext(
                extra_data={} if case == "auto_typed" else {"voice_delegate": True}
            ),
        )
    )
    claimed = await claim_due_candidate(
        worker_id="presentation-fixture",
        work_item_id=receipt.work_item_id,
        lease_seconds=60,
    )
    assert claimed is not None
    run_id = "presentation-run"
    proof = {
        "version": "work-proposal-v1",
        "capability_key": (
            "integral_update_app" if surface == "home" else "integral_update_dashboard"
        ),
        "work_item_id": claimed.work_item_id,
        "run_id": run_id,
        "principal_id": me,
        "workspace_id": ws,
        "thread_id": thread.id,
        "attempt": claimed.attempt,
        "effect_key": "presentation-effect",
    }
    payload = {
        "app_id": app_id,
        "dashboard_id": board.id,
        "expected_dashboard_updated_at": board.updated_at,
        "name": "Herbarium overview",
    }
    if surface == "home":
        payload = {
            "app_id": app_id,
            "home": {**app.home_composition, "title": "Herbarium overview"},
            "expected_home_revision": home_revision_token(app),
        }
    with bind_proposal_origin(proof):
        staged = await create_staged_change(
            user_id=me,
            workspace_id=ws,
            session_id=thread.id,
            kind="update_app" if surface == "home" else "update_dashboard",
            summary="Rename dashboard",
            diff_human="Rename to Herbarium overview",
            diff_machine=payload,
            payload=payload,
        )
    _, parked = await propose_work_approval_unit(
        work_item_id=claimed.work_item_id,
        lease_token=claimed.lease_token,
        lease_fence=claimed.lease_fence,
        run_id=run_id,
        run_step_id="presentation-step",
        staging_token=staged.token,
        authority_digest="fixture",
    )
    assert parked.status == "waiting_for_human"
    if case == "stale":
        if surface == "home":
            app.home_revision += 1
            await app.save()
        else:
            board = await Dashboard.get(board.id)
            board.updated_at = "2099-01-01T00:00:00+00:00"
            await board.save()
    ctx = SimpleNamespace(
        attempt=claimed.attempt,
        effect_key="presentation-effect",
        principal_id=me,
        workspace_id=ws,
        thread_id=thread.id,
        work_item_id=claimed.work_item_id,
        run_id=run_id,
    )
    outcome = await apply_resident_permission(claimed, ctx, staged.token)
    if case.startswith("auto_"):
        assert outcome and outcome["consumed"], outcome
        assert await read_title() == "Herbarium overview"
        assert await apply_resident_permission(claimed, ctx, staged.token) is None
        return
    assert outcome is None
    assert await read_title() == "Specimen summary"
    if case not in {"approve", "reject"}:
        return
    from app.agentive.services.speech import service

    relay = native_voice.VoiceRelay(
        Browser(),
        Provider(),
        native_voice.VoiceScope(me, ws, "", thread.id),
        conversational_review=True,
    )
    relay.accepted = native_voice._AcceptedVoiceTurn(
        receipt.work_item_id, receipt.message_id, "Rename the dashboard"
    )
    review = await relay._current_review()
    assert (
        review
        and "Herbarium overview" in review.text
        and ("Specimen summary" if surface == "dashboard" else "Home title")
        in review.text
    )
    confirmation = VoiceConfirmation(review, ("fixture", "model", "byok", "fixture"))
    confirmation.phase = "capture"
    confirmation.audio.extend(b"\xff\x7f" * 48000)
    confirmation.capture_done.set()
    relay.review_confirmation = confirmation
    monkeypatch.setattr(
        service,
        "transcribe_voice_confirmation",
        AsyncMock(return_value="Yes, go ahead" if case == "approve" else "No thanks"),
    )
    await relay._capture_review_confirmation(confirmation)
    assert (await get_token(staged.token)).state == (
        "consumed" if case == "approve" else "revoked"
    )
    assert await read_title() == (
        "Herbarium overview" if case == "approve" else "Specimen summary"
    )
