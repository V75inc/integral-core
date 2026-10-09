"""A chat-resolved write cannot trigger a second unrequested model turn."""

from types import SimpleNamespace

import pytest

from app.services import prompt_queue as pq

pytestmark = [pytest.mark.unit, pytest.mark.smoke]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source,required", [("chat", False), ("card", True), (None, True)]
)
@pytest.mark.parametrize(
    "state,status", [("consumed", "approved"), ("revoked", "rejected")]
)
async def test_reconcile_retains_receipt_and_only_native_chat_owns_continuation(
    monkeypatch, source, required, state, status
):
    async def save():
        pass

    thread = SimpleNamespace(
        id="thread",
        user_id="user",
        workspace_id="workspace",
        provider_id="integral_native",
        prompt_queue={
            "status": "open",
            "items": [
                {
                    "id": "item",
                    "token": "token",
                    "kind": "staged_write",
                    "status": "pending",
                }
            ],
        },
        save=save,
    )
    staged = SimpleNamespace(
        state=state,
        decision_source=source,
        user_id="user",
        workspace_id="workspace",
        session_id="thread",
    )

    async def get(_token):
        return staged

    async def emit(**_kwargs):
        pass

    monkeypatch.setattr("app.agentive.staging.get_token", get)
    monkeypatch.setattr(pq, "emit_change_event", emit)
    result = await pq.get_open_queue_for_thread(user_id="user", thread=thread)
    assert not result["open"]
    assert result["resume_text"]
    assert result["resume_required"] is required
    assert thread.prompt_queue["items"][0]["status"] == status
    assert not (await pq.get_open_queue_for_thread(user_id="user", thread=thread))[
        "resume_text"
    ]


@pytest.mark.parametrize("field", ["user_id", "workspace_id", "session_id"])
def test_foreign_decision_cannot_suppress_host_continuation(field):
    thread = SimpleNamespace(
        id="thread", workspace_id="workspace", provider_id="integral_native"
    )
    staged = SimpleNamespace(
        user_id="user",
        workspace_id="workspace",
        session_id="thread",
        decision_source="chat",
    )
    setattr(staged, field, "foreign")
    assert pq._verified_decision_source(staged, user_id="user", thread=thread) is None


def test_questions_or_mixed_review_keep_host_continuation():
    approved = {"kind": "staged_write", "status": "approved", "decision_source": "chat"}
    assert not pq._host_resume_required({"items": [approved]})
    for other in (
        {"kind": "question", "status": "answered"},
        {**approved, "decision_source": "card"},
        {**approved, "status": "cancelled"},
    ):
        assert pq._host_resume_required({"items": [approved, other]})
