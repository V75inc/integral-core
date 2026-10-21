"""Only unfinished reviewed work starts another model turn."""

from types import SimpleNamespace

import pytest

from app.services import prompt_queue as pq

pytestmark = [pytest.mark.unit, pytest.mark.smoke]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source,required", [("chat", False), ("card", False), (None, False)]
)
@pytest.mark.parametrize(
    "state,status", [("consumed", "approved"), ("revoked", "rejected")]
)
async def test_terminal_write_retains_receipt_without_model_continuation(
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


@pytest.mark.parametrize("status", ["answered", "skipped"])
def test_questions_keep_host_continuation(status):
    """Answered or skipped questions unlock the waiting task."""
    approved = {"kind": "staged_write", "status": "approved", "decision_source": "chat"}
    question = {"kind": "question", "status": status}
    assert pq._host_resume_required({"items": [approved, question]})
    assert not pq._host_resume_required(
        {"items": [approved, question], "close_reason": "cancelled"}
    )


@pytest.mark.parametrize("status", ["approved", "rejected", "cancelled"])
def test_terminal_ordinary_card_needs_no_model(status):
    """Applied or refused ordinary writes terminate with host feedback."""
    item = {
        "kind": "staged_write",
        "status": status,
        "decision_source": "card",
        "write_kind": "create_entry",
    }
    assert not pq._host_resume_required({"items": [item]})


@pytest.mark.parametrize("kind", ["design_proposal", "propose_profile_revision"])
def test_approved_unfinished_work_requires_continuation(kind):
    """Design builds and reviewed draft publication retain their continuation."""
    item = {
        "kind": "staged_write",
        "status": "approved",
        "decision_source": "card",
        "write_kind": kind,
        "profile_revision_review": "reviewed diff",
    }
    assert pq._host_resume_required({"items": [item]})
    assert not pq._host_resume_required({"items": [{**item, "status": "rejected"}]})
    assert not pq._host_resume_required(
        {"items": [{**item, "decision_source": "chat"}]}
    )
    assert not pq._host_resume_required({"items": [item], "close_reason": "cancelled"})


def test_unreviewed_profile_revision_does_not_start_publication():
    """A missing persisted diff remains an explicit review limitation."""
    assert not pq._host_resume_required(
        {
            "items": [
                {
                    "kind": "staged_write",
                    "status": "approved",
                    "write_kind": "propose_profile_revision",
                }
            ]
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("action", ["mark_write", "cancel_all", "resolve_question"])
async def test_http_projection_preserves_continuation_decision(
    monkeypatch, required, action
):
    """HTTP adapters must not drop the service's no-model decision."""
    from unittest.mock import AsyncMock

    from app.agentive.api import prompt_queue as api

    result = {
        "ok": True,
        "matched": True,
        "closed": True,
        "queue": {},
        "resume_text": "Review completed",
        "resume_required": required,
    }
    thread = SimpleNamespace(id="thread", user_id="user")
    monkeypatch.setattr(api, "_resolve_user", lambda _request: "user")
    monkeypatch.setattr(api, "_load_thread", AsyncMock(return_value=thread))
    body = {"thread_id": "thread"}
    if action == "mark_write":
        body.update(token="token", status="approved")
        monkeypatch.setattr(pq, "mark_write_item", AsyncMock(return_value=result))
        endpoint = api.mark_write_endpoint
    elif action == "cancel_all":
        monkeypatch.setattr(pq, "cancel_all", AsyncMock(return_value=result))
        endpoint = api.cancel_all_endpoint
    else:
        body.update(item_id="item", skip=True)
        monkeypatch.setattr(pq, "resolve_question_item", AsyncMock(return_value=result))
        endpoint = api.resolve_question_endpoint
    request = SimpleNamespace(json=AsyncMock(return_value=body))
    assert (await endpoint(request))["resume_required"] is required
