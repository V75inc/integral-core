"""Scoped canonical chat receipts complement native checkpoint history."""

from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelRequest, ToolReturnPart

from app.agentive.harness.contracts import HarnessExecutionScope
from app.services.chat_providers.pydantic_ai_provider import (
    _host_staging_outcome_instructions,
    _refresh_staged_history,
    _scoped_transcript_receipts,
)


@pytest.mark.parametrize("state", ["consumed", "revoked", "expired"])
def test_host_task_uses_latest_reconciled_outcome_without_proposal_prose(state):
    def request(outcome):
        return ModelRequest(
            parts=[ToolReturnPart("integral_commit_batch", outcome, "call")]
        )

    latest = {
        **_envelope(state),
        "state_source": "current_core_staging",
        "summary": "Untrusted proposal prose must not become host instructions",
    }
    history = [request({**latest, "state": "consumed"}), request(latest)]
    instructions = _host_staging_outcome_instructions(history, conversation_id="thread")
    assert f'"state": "{state}"' in instructions
    assert "Untrusted proposal prose" not in instructions
    assert "not a new human request" in instructions
    assert "do not invite approval or restage it" in instructions


@pytest.mark.parametrize(
    "override",
    [
        {"state_source": "snapshot"},
        {"session_id": "foreign"},
        {"state": "pending"},
        {"state": "blessed"},
    ],
)
def test_host_task_cannot_use_unverified_foreign_or_unsettled_receipts(override):
    content = {**_envelope(), "state_source": "current_core_transcript", **override}
    history = [
        ModelRequest(parts=[ToolReturnPart("integral_commit_batch", content, "call")])
    ]
    assert _host_staging_outcome_instructions(history, conversation_id="thread") == ""


@pytest.fixture
def receipt_scope():
    return HarnessExecutionScope(
        tenant_id="workspace",
        principal_id="user",
        workspace_id="workspace",
        thread_id="thread",
        session_id="native-session",
        run_id="next-run",
        permission_revision="permission",
        capability_version="capability",
    )


def _envelope(state="consumed"):
    return {
        "_kind": "staged_change",
        "token": "token",
        "session_id": "thread",
        "state": state,
        "execute_result": {"entry_id": "saved-entry"},
        "consumed_nav": {"entryId": "saved-entry"},
    }


def _thread(monkeypatch, envelope=None):
    message = SimpleNamespace(
        role="assistant",
        thread_id="thread",
        parts=[{"type": "tool-call", "result": envelope or _envelope()}],
    )

    async def page(**kwargs):
        assert kwargs["limit"] == 100
        return [message], None

    thread = SimpleNamespace(
        user_id="user",
        workspace_id="workspace",
        provider_id="integral_native",
        provider_session_id="native-session",
        nodes_page=page,
    )

    async def get(identifier):
        assert identifier == "thread"
        return thread

    monkeypatch.setattr("app.models.nodes.ChatThread.get", get)
    return thread, message


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [True, False])
async def test_applied_record_identity_survives_live_token_and_restart(
    monkeypatch, receipt_scope, live
):
    _thread(monkeypatch)
    current = SimpleNamespace(
        user_id="user",
        workspace_id="workspace",
        session_id="thread",
        to_dict=lambda: {"state": "consumed"},
    )

    async def get_token(_token):
        return current if live else None

    monkeypatch.setattr("app.agentive.staging.get_token", get_token)
    history = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    "integral_create_entry",
                    {
                        "_kind": "staged_change",
                        "token": "token",
                        "session_id": "thread",
                        "state": "pending",
                    },
                    "call",
                )
            ]
        )
    ]
    result = await _refresh_staged_history(
        history, receipt_scope, conversation_id="thread"
    )
    content = result[0].parts[0].content
    assert content["state"] == "consumed"
    assert content["execute_result"]["entry_id"] == "saved-entry"
    assert content["state_source"] == (
        "current_core_staging" if live else "current_core_transcript"
    )
    assert history[0].parts[0].content["state"] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["user_id", "workspace_id", "provider_id", "provider_session_id"]
)
async def test_foreign_binding_never_supplies_receipt(
    monkeypatch, receipt_scope, field
):
    thread, _ = _thread(monkeypatch)
    setattr(thread, field, "foreign")
    assert (
        await _scoped_transcript_receipts(
            receipt_scope, {"token"}, conversation_id="thread"
        )
        == {}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "pending",
        "blessed",
        "foreign_session",
        "foreign_message",
        "human",
        "foreign_token",
    ],
)
async def test_only_server_terminal_matching_envelopes_are_authority(
    monkeypatch, receipt_scope, change
):
    envelope = _envelope()
    _, message = _thread(monkeypatch, envelope)
    if change in {"pending", "blessed"}:
        envelope["state"] = change
    elif change == "foreign_session":
        envelope["session_id"] = "foreign"
    elif change == "foreign_token":
        envelope["token"] = "foreign"
    elif change == "human":
        message.role = "user"
    else:
        message.thread_id = "foreign"
    assert (
        await _scoped_transcript_receipts(
            receipt_scope, {"token"}, conversation_id="thread"
        )
        == {}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("live_state", ["pending", "blessed", "revoked", "foreign"])
async def test_current_scoped_decision_wins_over_older_transcript(
    monkeypatch, receipt_scope, live_state
):
    _thread(monkeypatch)
    current = SimpleNamespace(
        user_id="foreign" if live_state == "foreign" else "user",
        workspace_id="workspace",
        session_id="thread",
        to_dict=lambda: {"state": live_state},
    )

    async def get_token(_token):
        return current

    monkeypatch.setattr("app.agentive.staging.get_token", get_token)
    history = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    "integral_create_entry",
                    {
                        "_kind": "staged_change",
                        "token": "token",
                        "session_id": "thread",
                        "state": "pending",
                    },
                    "call",
                )
            ]
        )
    ]
    content = (
        (
            await _refresh_staged_history(
                history, receipt_scope, conversation_id="thread"
            )
        )[0]
        .parts[0]
        .content
    )
    assert "execute_result" not in content
    if live_state == "foreign":
        assert content["error_code"] == "staging_state_unavailable"
    else:
        assert content["state"] == live_state


@pytest.mark.asyncio
async def test_rollback_receipt_cannot_be_hidden_on_restart(monkeypatch, receipt_scope):
    envelope = {
        **_envelope(),
        "rolled_back_at": "2026-10-07T00:00:00Z",
        "rollback_event_ids": ["rollback"],
    }
    _thread(monkeypatch, envelope)
    receipts = await _scoped_transcript_receipts(
        receipt_scope, {"token"}, conversation_id="thread"
    )
    assert receipts["token"]["rolled_back_at"] == envelope["rolled_back_at"]
    assert (
        await _scoped_transcript_receipts(
            receipt_scope, {"token"}, conversation_id="foreign"
        )
        == {}
    )
