"""Pure contract checks for durable chat submission identity."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.agentive.work import (
    ChatTurnExecutionContext,
    ChatTurnSubmissionRequest,
)
from app.schemas.api.ai_chat import SendMessageRequest
from app.services.app_operations.transaction_scope import (
    OperationTransactionUnavailable,
)
from app.services.chat_turn_submissions import (
    _request_fingerprint,
    _submission_identity,
)


def _request(**overrides) -> ChatTurnSubmissionRequest:
    values = {
        "principal_id": "user-1",
        "workspace_id": "workspace-1",
        "thread_id": "thread-1",
        "client_request_id": "00000000-0000-4000-8000-000000000001",
        "parts": [{"type": "text", "text": "hello"}],
    }
    values.update(overrides)
    return ChatTurnSubmissionRequest.model_validate(values)


def test_submission_identity_is_stable_and_scope_separated() -> None:
    identity = _submission_identity(
        principal_id="user-1",
        workspace_id="workspace-1",
        thread_id="thread-1",
        client_request_id="request-1",
    )

    assert identity == _submission_identity(
        principal_id="user-1",
        workspace_id="workspace-1",
        thread_id="thread-1",
        client_request_id="request-1",
    )
    assert identity[0].startswith("chat-turn:")
    assert identity[1].startswith("n.ChatMessage.")
    assert identity != _submission_identity(
        principal_id="user-1",
        workspace_id="workspace-2",
        thread_id="thread-1",
        client_request_id="request-1",
    )
    assert identity != _submission_identity(
        principal_id="user-1",
        workspace_id="workspace-1",
        thread_id="thread-2",
        client_request_id="request-1",
    )


def test_request_fingerprint_covers_message_and_metadata() -> None:
    original = _request()
    same = _request(provider_metadata={})
    changed_content = _request(parts=[{"type": "text", "text": "changed"}])
    changed_context = _request(provider_metadata={"page_context": {"route": "/"}})

    assert _request_fingerprint(original) == _request_fingerprint(same)
    assert _request_fingerprint(original) != _request_fingerprint(changed_content)
    assert _request_fingerprint(original) != _request_fingerprint(changed_context)


def test_execution_context_is_allowlisted_and_bounded() -> None:
    context = ChatTurnExecutionContext(
        system_context="trusted server context",
        focused_track_id="track-1",
        extra_data={"run_id": "run-1", "page_context": {"route_path": "/agent"}},
    )
    assert context.extra_data["run_id"] == "run-1"
    with pytest.raises(ValidationError):
        ChatTurnExecutionContext(extra_data={"image_urls": ["base64-data"]})
    with pytest.raises(ValidationError):
        ChatTurnExecutionContext(extra_data={"prompt": "duplicate user prompt"})
    with pytest.raises(ValidationError):
        ChatTurnExecutionContext(extra_data={"page_context": {"x": "x" * 200_000}})


def test_execution_context_changes_submission_fingerprint() -> None:
    first = _request(
        execution_context=ChatTurnExecutionContext(system_context="context A")
    )
    changed = _request(
        execution_context=ChatTurnExecutionContext(system_context="context B")
    )
    assert _request_fingerprint(first) != _request_fingerprint(changed)


@pytest.mark.parametrize(
    "request_id",
    ["", "contains whitespace", "line\nbreak", "x" * 129],
)
def test_client_request_id_is_bounded_and_header_safe(request_id: str) -> None:
    with pytest.raises(ValidationError):
        _request(client_request_id=request_id)


def test_chat_api_accepts_optional_bounded_client_request_id() -> None:
    parsed = SendMessageRequest.model_validate(
        {"text": "hello", "client_request_id": "request-1"}
    )
    assert parsed.client_request_id == "request-1"
    with pytest.raises(ValidationError):
        SendMessageRequest.model_validate(
            {"text": "hello", "client_request_id": "not a safe id"}
        )


def test_submission_payload_must_contain_a_user_message_part() -> None:
    with pytest.raises(ValidationError):
        _request(parts=[])


@pytest.mark.asyncio
async def test_submission_fails_closed_without_shared_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import chat_turn_submissions

    monkeypatch.setattr(
        chat_turn_submissions, "graph_transaction_available", lambda: False
    )
    with pytest.raises(OperationTransactionUnavailable):
        await chat_turn_submissions.submit_chat_turn(_request())
