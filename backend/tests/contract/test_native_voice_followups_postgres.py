"""Voice continuations use only scoped, durable native chat commands."""

import os

import pytest
import pytest_asyncio
from jvspatial.core.context import get_default_context

from app.agentive.harness.turn_input import load_turn_input_capsule
from app.agentive.services.work_items import cancel_work_item
from app.agentive.work_models import WorkItem
from app.api.errors import InsufficientPermissionsError, ResourceConflictError
from app.schemas.agentive.chat_followups import ChatFollowUpRequest
from app.schemas.agentive.steering import SteeringCorrectionRequest
from app.schemas.agentive.work import (
    ChatTurnExecutionContext,
    ChatTurnSubmissionRequest,
)
from app.services import chat_voice_fence
from app.services.chat_followups import (
    accept_chat_follow_up,
    chat_follow_ups_paused,
    list_chat_follow_ups,
    promote_next_chat_follow_up,
    resume_chat_follow_ups,
)
from app.services.chat_steering import _decode, _inbox_id, accept_steering_correction
from app.services.chat_threads import create_thread
from app.services.chat_turn_submissions import submit_chat_turn
from app.services.native_voice import VoiceScope
from app.services.native_voice_followups import (
    resolve_voice_follow_up_target,
    route_voice_follow_up,
)

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def voice_thread(authenticated_client, test_user, monkeypatch):
    """Create one native thread owned by a real authenticated test principal."""
    if os.getenv("INTEGRAL_TEST_DB") not in ("postgres", "postgresql"):
        pytest.skip("Voice follow-up authority requires real PostgreSQL")
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "29" * 32)
    response = await authenticated_client.post(
        "/api/workspaces", json={"name": "Isolated voice follow-ups"}
    )
    assert response.status_code == 200
    workspace = response.json()["workspace"]["id"]
    thread = await create_thread(
        user_id=test_user.user_id, workspace_id=workspace, provider_id="integral_native"
    )
    scope = VoiceScope(test_user.user_id, workspace, "", thread.id)
    keys = {
        "principal_id": scope.principal,
        "workspace_id": scope.workspace,
        "thread_id": scope.thread,
    }
    return scope, keys


async def _submit(keys, request_id, *, voice):
    return await submit_chat_turn(
        ChatTurnSubmissionRequest(
            **keys,
            client_request_id=request_id,
            parts=[{"type": "text", "text": "Count current records."}],
            execution_context=ChatTurnExecutionContext(
                extra_data={"voice_delegate": True} if voice else {}
            ),
        )
    )


async def _read_item(item_id):
    return await WorkItem.get(f"o.WorkItem.{item_id}")


async def test_voice_queue_reuses_canonical_fifo_and_preserves_approval_boundary(
    voice_thread,
):
    """A queued spoken request is not a typed request or a second active turn."""
    scope, keys = voice_thread
    first = await _submit(keys, "typed-first", voice=False)
    target = await resolve_voice_follow_up_target(scope)
    assert target is not None
    text = "Now summarize our open tasks."
    result = await route_voice_follow_up(
        scope, "new request", text, target=target, operation="queue"
    )
    assert (
        result.status == "queued"
        and result.predecessor_work_item_id == first.work_item_id
    )
    assert (
        await route_voice_follow_up(
            scope, "new request", text, target=target, operation="queue"
        )
        == result
    )
    with pytest.raises(ResourceConflictError, match="different content"):
        await route_voice_follow_up(
            scope, "new request", "Changed request", target=target, operation="queue"
        )
    [spoken] = await list_chat_follow_ups(**keys)
    assert spoken == ChatFollowUpRequest(
        client_request_id=result.client_request_id,
        text=text,
        after_work_item_id=first.work_item_id,
    )
    assert set(spoken.model_dump()) == {
        "client_request_id",
        "text",
        "entity_refs",
        "after_work_item_id",
    }
    with pytest.raises(ValueError):
        ChatFollowUpRequest.model_validate(
            {**spoken.model_dump(), "voice_origin": True}
        )
    with pytest.raises(ResourceConflictError, match="different content"):
        await accept_chat_follow_up(**keys, request=spoken)
    with pytest.raises(ResourceConflictError, match="converted"):
        await accept_steering_correction(
            first.work_item_id,
            scope.principal,
            SteeringCorrectionRequest(
                client_request_id=spoken.client_request_id,
                text=text,
                from_follow_up_queue=True,
            ),
        )

    assert await promote_next_chat_follow_up({}) is None
    assert not (await _read_item(first.work_item_id)).cancel_requested_at
    await cancel_work_item(first.work_item_id)
    assert await chat_follow_ups_paused(**keys)
    assert await promote_next_chat_follow_up({}) is None
    await resume_chat_follow_ups(**keys)
    promoted = await promote_next_chat_follow_up({})
    assert promoted is not None and promoted.work_item_id != first.work_item_id
    assert promoted.client_request_id == result.client_request_id
    item = await _read_item(promoted.work_item_id)
    capsule = await load_turn_input_capsule(
        **keys,
        work_item_id=promoted.work_item_id,
        capsule_id=item.input_payload["capsule_id"],
        expected_digest=item.input_payload["capsule_digest"],
    )
    assert capsule.execution_context.extra_data == {"voice_delegate": True}
    assert capsule.execution_context.host_control is None
    assert await list_chat_follow_ups(**keys) == []


async def test_explicit_refinement_is_one_receipt_on_exact_voice_item(voice_thread):
    """Store exactly one correction, without implying it was consumed."""
    scope, keys = voice_thread
    first = await _submit(keys, "voice-first", voice=True)
    target = await resolve_voice_follow_up_target(scope)
    phrase = "Keep that same result to one sentence."
    receipt = await route_voice_follow_up(
        scope, "refine", phrase, target=target, operation="steer"
    )
    assert receipt.status == "steering_accepted"
    assert (
        await route_voice_follow_up(
            scope, "refine", phrase, target=target, operation="steer"
        )
        == receipt
    )
    with pytest.raises(ResourceConflictError, match="already used"):
        await route_voice_follow_up(
            scope, "refine", "Use ten pages.", target=target, operation="steer"
        )
    inbox_id = _inbox_id(first.work_item_id)
    inbox = await get_default_context().database.get("object", inbox_id)
    assert _decode(inbox_id, inbox["context"]["payload_ciphertext"]) == {
        receipt.client_request_id: phrase
    }
    assert await list_chat_follow_ups(**keys) == []
    assert (await _read_item(first.work_item_id)).status == "queued"


async def test_closed_typed_stale_and_other_session_cannot_be_steered(voice_thread):
    """Never retarget a stale request or inherit a typed worker’s authority."""
    scope, keys = voice_thread
    typed = await _submit(keys, "typed-first", voice=False)
    target = await resolve_voice_follow_up_target(scope)
    with pytest.raises(ResourceConflictError, match="spoken refinement"):
        await route_voice_follow_up(
            scope, "typed", "Expand that.", target=target, operation="steer"
        )
    other_session = VoiceScope(scope.principal, scope.workspace, "", scope.thread)
    with pytest.raises(InsufficientPermissionsError):
        await route_voice_follow_up(
            other_session, "same", "Do the next task.", target=target, operation="queue"
        )

    # The exact WorkItem row is still queued. A changed primary pointer
    # alone is sufficient to reject the old snapshot at the write boundary.
    database = get_default_context().database
    await database.find_one_and_update(
        "node",
        {"id": scope.thread},
        {"$set": {"context.active_work_item_id": "different-current-item"}},
    )
    assert (await _read_item(typed.work_item_id)).status == "queued"
    with pytest.raises(ResourceConflictError, match="no longer active"):
        await route_voice_follow_up(
            scope, "lost-pointer", "Do the next task.", target=target, operation="queue"
        )
    await database.find_one_and_update(
        "node",
        {"id": scope.thread},
        {"$set": {"context.active_work_item_id": typed.work_item_id}},
    )

    # Even though the original request still exists in the same chat, a
    # replacement that wins before the mutation must not inherit the request.
    await cancel_work_item(typed.work_item_id)
    voice = await _submit(keys, "voice-replacement", voice=True)
    with pytest.raises(ResourceConflictError, match="no longer active"):
        await route_voice_follow_up(
            scope, "same", "Do the next task.", target=target, operation="queue"
        )
    current = await resolve_voice_follow_up_target(scope)
    assert current is not None and current.fence.work_item_id == voice.work_item_id
    database = get_default_context().database
    await database.find_one_and_update(
        "object",
        {"id": f"o.WorkItem.{voice.work_item_id}"},
        {"$set": {"context.steering_closed": True}},
    )
    with pytest.raises(ResourceConflictError, match="no longer accepts steering"):
        await route_voice_follow_up(
            scope, "late", "Make it short.", target=current, operation="steer"
        )
    assert await database.get("object", _inbox_id(voice.work_item_id)) is None
    assert await list_chat_follow_ups(**keys) == []


async def test_scope_and_profile_are_rechecked_at_commit_and_promotion(
    voice_thread, monkeypatch
):
    """Revoked authority is denied at commit and never survives promotion."""
    scope, keys = voice_thread
    first = await _submit(keys, "voice-first", voice=True)
    target = await resolve_voice_follow_up_target(scope)
    database = get_default_context().database

    with monkeypatch.context() as fixture:

        async def revoked(*_args: str) -> str:
            return "none"

        fixture.setattr(chat_voice_fence, "can_access_workspace", revoked)
        with pytest.raises(InsufficientPermissionsError):
            await route_voice_follow_up(
                scope, "revoked", "Do the next task.", target=target, operation="queue"
            )
    assert await list_chat_follow_ups(**keys) == []

    receipt = await route_voice_follow_up(
        scope, "allowed", "Do the next task.", target=target, operation="queue"
    )
    assert receipt.status == "queued"
    await cancel_work_item(first.work_item_id)
    await resume_chat_follow_ups(**keys)
    with monkeypatch.context() as fixture:
        fixture.setattr(chat_voice_fence, "can_access_workspace", revoked)
        assert await promote_next_chat_follow_up({}) is None
    assert len(await list_chat_follow_ups(**keys)) == 1
    await database.find_one_and_update(
        "node",
        {"id": scope.thread},
        {"$set": {"context.agent_profile_key": "different-profile"}},
    )
    assert await promote_next_chat_follow_up({}) is None
    assert len(await list_chat_follow_ups(**keys)) == 1
    named_scope = VoiceScope(
        scope.principal, scope.workspace, "different-profile", scope.thread
    )
    with pytest.raises(
        ResourceConflictError, match="unavailable for selected profiles"
    ):
        await resolve_voice_follow_up_target(named_scope)
